"""
parse_advances.py

Parses all "advance" entries from EU5-style script files in a given directory.

Schema reference (all fields optional except the block ID itself):
    age, icon, requires, government, country_type
    allow       = { <triggers> }
    potential   = { <triggers> }
    for         = adm | dip | mil
    unlock_unit, unlock_ability, unlock_interaction,
    unlock_country_interaction, unlock_relation_type,
    unlock_building, unlock_law, unlock_levy,
    unlock_government_reform, unlock_casus_belli,
    unlock_subject_type, unlock_production_method
    allow_children = yes | no
    modifier_while_progressing = { potential_trigger = {...}  scale = <expr>  <modifiers> }
    ai_weight   = { <triggers/math> }
    <modifiers> — any key = value pair not matching a known key above

Scripted variables (@name = value) declared at the top of each file are
resolved before parsing and before storing _source_text, so every @reference
is replaced with its literal value everywhere.

Each parsed advance dict contains:
    _name        : the block identifier
    _source_text : the original block text with @variables already substituted
    <known keys> : their parsed values (scalars, nested dicts, or lists)
    modifiers    : dict of modifier_key -> value (or list of values)

age_1_traditions advances are NOT included in the returned parsed list.
They are written verbatim (with @variables substituted) to the output file.

The --output file:
  - age_1_traditions advances : copied verbatim (variables substituted)
  - all other advances        : copied from source (variables substituted),
                                with lines belonging to modifiers /
                                modifier_while_progressing / ai_weight /
                                unlock_* removed

Usage:
    python parse_advances.py <directory> [--output advances.txt]
"""

import re
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# Known advance keys  (everything else is a modifier)
# ---------------------------------------------------------------------------

SCALAR_KEYS: frozenset[str] = frozenset({
    "age",
    "icon",
    "requires",
    "government",
    "country_type",
    "for",
    "allow_children",
    "depth",
    "content_priority",
    "starting_technology_level",  # structural: starting tech level, not a modifier
    "research_cost",       # structural: cost to research the advance (1.3)
    "in_tree_of",          # structural: tree-tree placement reference (1.3)
    "pure_tooltip_entry",  # cosmetic tooltip-only line (1.3)
    "unlock_road_type",
    "unlock_employment_system",
    "unlock_unit",
    "unlock_ability",
    "unlock_interaction",
    "unlock_country_interaction",
    "unlock_estate_privilege",
    "unlock_relation_type",
    "unlock_building",
    "unlock_law",
    "unlock_policy",
    "unlock_levy",
    "unlock_heir_selection",
    "unlock_government_reform",
    "unlock_casus_belli",
    "unlock_cabinet_action",
    "unlock_subject_type",
    "unlock_production_method",
    "unlock_diplomacy",
    "unlock_town_rights",
    "unlock_chivalric_order",  # unlock: chivalric order (1.3)
})

BLOCK_KEYS: frozenset[str] = frozenset({
    "allow",
    "potential",
    "modifier_while_progressing",
    "ai_weight",
    "ai_preference_tags",
})

ALL_KNOWN_KEYS: frozenset[str] = SCALAR_KEYS | BLOCK_KEYS

_UNLOCK_KEYS: frozenset[str] = frozenset({k for k in SCALAR_KEYS if k.startswith("unlock_")})
OUTPUT_STRIP_KEYS: frozenset[str] = _UNLOCK_KEYS | frozenset({
    "modifier_while_progressing",
    "ai_weight",
})

TRADITIONS_AGE = "age_1_traditions"


# ---------------------------------------------------------------------------
# Synergy-set tagging
# ---------------------------------------------------------------------------
# Each advance is classified into one theme. Collecting several picks of the
# same theme awards a set bonus (see rl_take_<tag> in rl_manual_effects.txt).

TAG_ORDER = ("military", "naval", "economic", "admin")

TAG_LABEL = {
    "military": "#R Military#!",
    "naval":    "#B Naval#!",
    "economic": "#Y Economic#!",
    "admin":    "#P Administrative#!",
}

_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "military": (
        "army_", "land_morale", "discipline", "siege", "regiment", "levy",
        "manpower", "fort", "garrison", "mercenar", "drill", "conscription",
        "cavalry", "infantry", "artillery", "combat", "assault", "war_",
        "military", "soldier", "recruit", "hussar", "grenadier", "musketeer",
    ),
    "naval": (
        "navy_", "naval_", "ship_", "sailor", "blockade", "galley", "frigate",
        "privateer", "maritime", "marine", "dock", "harbor", "galleon",
        "caravel", "carrack", "hulk", "seazone", "coast",
    ),
    "economic": (
        "output_modifier", "trade_", "tax_", "production", "mint", "bank",
        "bond", "income", "market", "merchant", "goods", "rgo", "tariff",
        "inflation", "loan", "_gold", "build_buildings", "food", "money",
        "workshop", "manufactory", "mill", "export", "import",
    ),
    "admin": (
        "bureaucracy", "control", "integration", "government", "law", "estate",
        "stability", "cabinet", "culture", "literacy", "development", "reform",
        "administrat", "diplomat", "legitimacy", "prestige", "tolerance",
        "religious", "devotion", "colon", "policy",
    ),
}


def _classify_tag(advance: dict) -> str:
    """Return the synergy tag ('military'|'naval'|'economic'|'admin') that best
    matches an advance's modifiers and unlocks. Defaults to 'admin'."""
    tokens: list[str] = list(advance.get("modifiers", {}).keys())
    for uk in _UNLOCK_KEYS:
        if uk in advance:
            v = advance[uk]
            tokens.extend(v if isinstance(v, list) else [v])
    blob = " ".join(str(t) for t in tokens).lower()
    scores = {tag: sum(blob.count(kw) for kw in kws)
              for tag, kws in _TAG_KEYWORDS.items()}
    best = max(TAG_ORDER, key=lambda t: scores[t])
    return best if scores[best] > 0 else "admin"


# ---------------------------------------------------------------------------
# Scripted variable resolution
# ---------------------------------------------------------------------------

# Matches top-level lines like:   @fort_limit_modifier_increase = 0.1
_SCRIPTED_VAR_RE = re.compile(
    r'^\s*(@[A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s#]+)',
    re.MULTILINE,
)

# Matches an @reference used as a value anywhere in text
_VAR_REF_RE = re.compile(r'@[A-Za-z_][A-Za-z0-9_]*')


def _parse_scripted_vars(text: str) -> dict[str, str]:
    """
    Scan *text* for top-level scripted variable declarations (@name = value)
    and return them as a {name: value} dict, e.g. {"@fort_limit": "0.1"}.
    """
    return {m.group(1): m.group(2) for m in _SCRIPTED_VAR_RE.finditer(text)}


def _resolve_vars(text: str, vars: dict[str, str]) -> str:
    """
    Replace every @reference in *text* with its declared value.
    Unknown references are left unchanged.
    """
    if not vars:
        return text
    return _VAR_REF_RE.sub(lambda m: vars.get(m.group(0), m.group(0)), text)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

_SCALAR_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\s*([<>=!]{1,2})\s*([^\s{}\n#]+)'
)
_BLOCK_OPEN_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{'
)


def _extract_block(text: str, open_brace_pos: int) -> tuple[str, int]:
    """Return (body_inside_braces, index_after_closing_brace)."""
    depth = 0
    j = open_brace_pos
    length = len(text)
    while j < length:
        ch = text[j]
        if ch == '#':
            while j < length and text[j] != '\n':
                j += 1
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[open_brace_pos + 1:j], j + 1
        j += 1
    raise ValueError("Unmatched '{' in block")


def _find_top_level_blocks(text: str) -> list[tuple[str, str, str]]:
    """
    Return [(block_name, block_body, raw_text), ...] for every top-level
    `identifier = { ... }` in *text*.
    """
    results = []
    i = 0
    length = len(text)

    while i < length:
        if text[i] == '#':
            while i < length and text[i] != '\n':
                i += 1
            continue

        m = _BLOCK_OPEN_RE.match(text, i)
        if m:
            name = m.group(1)
            block_start = i
            try:
                body, end = _extract_block(text, m.end() - 1)
                raw = text[block_start:end].rstrip()
                results.append((name, body, raw))
                i = end
            except ValueError:
                break
        else:
            i += 1

    return results


# ---------------------------------------------------------------------------
# Generic field parser
# ---------------------------------------------------------------------------

def _parse_fields(body: str) -> dict:
    """
    Parse the interior of a block into a dict.
    Repeated keys become lists. Sub-blocks are parsed recursively.
    Comparison operators are kept as part of the value string.
    @variables must already be resolved in *body* before calling this.
    """
    fields: dict = {}
    length = len(body)
    i = 0

    def _set(key: str, val):
        if key in fields:
            existing = fields[key]
            if isinstance(existing, list):
                existing.append(val)
            else:
                fields[key] = [existing, val]
        else:
            fields[key] = val

    while i < length:
        ch = body[i]

        if ch in ' \t\r\n':
            i += 1
            continue

        if ch == '#':
            while i < length and body[i] != '\n':
                i += 1
            continue

        m = _BLOCK_OPEN_RE.match(body, i)
        if m:
            key = m.group(1)
            try:
                sub_body, end = _extract_block(body, m.end() - 1)
                _set(key, _parse_fields(sub_body))
                i = end
            except ValueError:
                break
            continue

        m = _SCALAR_RE.match(body, i)
        if m:
            key = m.group(1)
            op  = m.group(2)
            val = m.group(3)
            stored = val if op == '=' else f"{op} {val}"
            _set(key, stored)
            i = m.end()
            continue

        i += 1

    return fields


# ---------------------------------------------------------------------------
# Advance-specific post-processing
# ---------------------------------------------------------------------------

def _structure_advance(name: str, raw_fields: dict, source_text: str) -> dict:
    """
    Split a raw _parse_fields dict into the well-typed Advance schema.
    source_text must already have @variables substituted.
    """
    advance: dict = {
        "_name": name,
        "_source_text": source_text,
    }

    modifiers: dict = {}

    for key, value in raw_fields.items():
        if key in SCALAR_KEYS or key in BLOCK_KEYS:
            advance[key] = value
        else:
            if key in modifiers:
                existing = modifiers[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    modifiers[key] = [existing, value]
            else:
                modifiers[key] = value

    advance["modifiers"] = modifiers
    return advance


# ---------------------------------------------------------------------------
# Output text filter
# ---------------------------------------------------------------------------

INJECTED_MODIFIER = "\trl_roll_mtd = 1"

# NOTE: dummy_parent_advance deliberately has NO rl_roll_mtd. The research
# detection counts modifier:rl_roll_mtd (each real advance grants 1), so the
# parent and per-unlock dummies must not carry it or they would skew the count.
DUMMY_PARENT_ADVANCE = """dummy_parent_advance = {
\tage = age_1_traditions
\tdepth = 0
\tstarting_technology_level = 4
}"""


def _strip_advance_text(advance: dict) -> str:
    """
    Return the advance's source text (_source_text, already @-resolved) with:
      - lines AND their trailing newlines belonging to modifiers / unlock_* /
        ai_weight / modifier_while_progressing removed entirely
      - omen_strength_modifier = 0.0001 injected before the closing brace
        when any modifiers were removed
    """
    keys_to_strip: set[str] = set(OUTPUT_STRIP_KEYS) | set(advance["modifiers"].keys())
    had_modifiers = bool(advance["modifiers"]) or bool(set(advance.keys()) & _UNLOCK_KEYS)

    key_pattern = re.compile(
        r'^[ \t]*(?:' + '|'.join(re.escape(k) for k in keys_to_strip) + r')[ \t]*[=<>!]'
    )

    text = advance["_source_text"]
    skip_depth = 0
    result: list[str] = []
    i = 0

    while i < len(text):
        end = text.find('\n', i)
        if end == -1:
            line = text[i:]
            line_with_nl = line
            i = len(text)
        else:
            line = text[i:end]
            line_with_nl = text[i:end + 1]
            i = end + 1

        if skip_depth > 0:
            skip_depth += line.count('{') - line.count('}')
            continue  # drop line and its newline

        if key_pattern.match(line):
            skip_depth += line.count('{') - line.count('}')
            skip_depth = max(skip_depth, 0)
            continue  # drop line and its newline

        result.append(line_with_nl)

    output = ''.join(result)

    # Collapse runs of blank lines left by removed entries down to one
    output = re.sub(r'\n{3,}', '\n\n', output)

    # Insert the injected modifier just before the final closing brace
    output = re.sub(r'(\n}\s*)$', '\n' + INJECTED_MODIFIER + r'\1', output)

    return output


def _generate_dummy_advances(advance: dict) -> str:
    """
    Generate a single dummy advance block containing every unlock_* line
    present on *advance*:

        dummy_<advance_name> = {
            age = age_1_traditions
            icon = <icon>
            <unlock line 1 extracted verbatim from _source_text>
            <unlock line 2>
            ...
            potential = { has_variable = flag_dummy_<advance_name> }
            requires = dummy_parent_advance
        }

    Returns the dummy block text, or an empty string if the advance has no unlocks.
    """
    if not (set(advance.keys()) & _UNLOCK_KEYS):
        return ""

    name = advance["_name"]
    icon = advance.get("icon", "")

    # Extract all unlock lines verbatim from the resolved source text
    unlock_line_pattern = re.compile(
        r'^[ \t]*(?:' + '|'.join(re.escape(k) for k in _UNLOCK_KEYS) + r')[ \t]*[=<>!][^\n]*',
        re.MULTILINE,
    )
    unlock_lines = unlock_line_pattern.findall(advance["_source_text"])

    lines = [f"dummy_{name} = {{", f"\tage = {TRADITIONS_AGE}"]
    if icon:
        lines.append(f"\ticon = {icon}")
    for unlock_line in unlock_lines:
        lines.append(f"\t{unlock_line.strip()}")
    lines += [
        f"\tpotential = {{ has_variable = flag_dummy_{name} }}",
        f"\trequires = dummy_parent_advance",
        f"}}",
    ]

    return "\n".join(lines)


class ParseResult:
    """
    advances        — parsed dicts for non-traditions advances
    traditions_raw  — verbatim block strings for age_1_traditions advances
                      (@variables already substituted)
    """
    def __init__(self):
        self.advances: list[dict] = []
        self.traditions_raw: list[str] = []


def parse_advances_from_text(text: str, result: ParseResult) -> None:
    """
    Parse all advances in *text*, appending into *result* in-place.

    Scripted variables (@name = value) are extracted from *text* first and
    substituted throughout before any parsing or raw-text storage occurs.

    age_1_traditions advances go to result.traditions_raw (verbatim, resolved);
    all others go to result.advances (structured dict, resolved).
    """
    vars = _parse_scripted_vars(text)
    vars.setdefault("@fort_limit_modifier_increase", "0.1")
    resolved_text = _resolve_vars(text, vars)

    for name, body, raw_text in _find_top_level_blocks(resolved_text):
        fields = _parse_fields(body)
        if not (fields.keys() & ALL_KNOWN_KEYS):
            continue

        if fields.get("age") == TRADITIONS_AGE:
            result.traditions_raw.append(raw_text)
        else:
            result.advances.append(_structure_advance(name, fields, raw_text))


def parse_advances_from_directory(directory: str) -> ParseResult:
    """Recursively scan *directory* and return a ParseResult."""
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    result = ParseResult()

    for path in sorted(base.rglob('*')):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            print(f"  [WARN] Could not read {path}: {e}")
            continue

        before = len(result.advances) + len(result.traditions_raw)
        parse_advances_from_text(text, result)
        after  = len(result.advances) + len(result.traditions_raw)
        if after > before:
            print(f"  {path.name}: {after - before} advance(s)")

    return result


# ---------------------------------------------------------------------------
# Localization file parser
# ---------------------------------------------------------------------------

_LOC_LINE_RE = re.compile(r'^\ ([A-Za-z0-9_]+):\s*"(.*)"\s*(?:#.*)?$')


def _parse_single_loc_file(path: Path, loc: dict[str, str]) -> None:
    """Parse one .yml loc file into *loc*, skipping already-seen keys."""
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.strip().startswith('#') or line.strip().startswith('l_'):
                    continue
                m = _LOC_LINE_RE.match(line)
                if m:
                    loc.setdefault(m.group(1), m.group(2))
    except OSError as e:
        print(f"  [WARN] Could not read loc file {path}: {e}")


def parse_loc_dir(directory: str) -> dict[str, str]:
    """
    Recursively scan *directory* for .yml files and merge all localization
    keys into a single dict. First occurrence of a key wins.
    """
    loc: dict[str, str] = {}
    base = Path(directory)
    if not base.is_dir():
        print(f"  [WARN] Loc directory not found: {directory}")
        return loc
    for yml_path in sorted(p for p in base.rglob("*.yml") if p.is_file()):
        _parse_single_loc_file(yml_path, loc)
    print(f"  Loaded {len(loc)} loc keys from {directory}")
    return loc


def _unlock_value_to_label(value: str) -> str:
    """
    Convert a snake_case unlock value to a human-readable label:
      - If the first segment is a single letter, strip it (e.g. a_handgonners -> handgonners)
      - If the last segment is a number, replace it with "Level N" (e.g. a_legionaries_3 -> Legionaries Level 3)
      - Remaining words are Title Cased
      - Known phrase substitutions are applied afterward: "Levy A" -> "Levy", "Cb" -> "Casus Belli"
    """
    parts = value.split('_')

    # Strip leading single-letter prefix
    if len(parts) > 1 and len(parts[0]) == 1 and parts[0].isalpha():
        parts = parts[1:]

    # Handle trailing number as level
    if len(parts) > 1 and parts[-1].isdigit():
        level = parts[-1]
        parts = parts[:-1] + ['Level', level]

    label = ' '.join(word.capitalize() if not word.isdigit() else word for word in parts)

    # Known phrase substitutions
    label = re.sub(r'\bLevy A\b', 'Levy', label)
    label = re.sub(r'\bCb\b', 'Casus Belli', label)

    return label


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse EU5-style advance entries from script files in a directory."
    )
    parser.add_argument("directory", help="Directory to scan.")
    parser.add_argument(
        "--loc-dir", "-l", default=None,
        help="Path to a localization directory; all .yml files are searched for keys."
    )
    args = parser.parse_args()

    print(f"Scanning: {args.directory}")
    result = parse_advances_from_directory(args.directory)
    print(f"\nTotal advances parsed  : {len(result.advances)}")
    print(f"Traditions pass-through: {len(result.traditions_raw)}")

    # Main output: traditions, advances, dummy_parent_advance, dummy advances (bottom)
    sections: list[str] = []
    sections.extend(
        re.sub(r'(\n}\s*)$', '\n' + INJECTED_MODIFIER + r'\1', t)
        for t in result.traditions_raw
    )
    sections.extend(_strip_advance_text(a) for a in result.advances)

    dummy_sections = [
        _generate_dummy_advances(a)
        for a in result.advances
        if set(a.keys()) & _UNLOCK_KEYS
    ]
    sections.append(DUMMY_PARENT_ADVANCE)
    sections.extend(dummy_sections)

    out_dir = Path("../in_game/common/advances")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rl_advances.txt"
    out_path.write_text("\n\n".join(sections), encoding='utf-8-sig')
    print(f"Output written to: {out_path}")

    # Random list output
    rl_lines = [
        f"\t\t1 = {{ trigger = {{ NOT = {{ has_variable = var_{a['_name']} }} }}"
        f" set_variable = {{ name = rl_event_$v$ value = {i} }} }}"
        for i, a in enumerate(result.advances)
    ]
    rl_output = (
        "rl_rand_se = {\n"
        "\trandom_list = {\n"
        + "\n".join(rl_lines) + "\n"
        "\t}\n"
        "}"
    )
    rl_dir = Path("../in_game/common/scripted_effects")
    rl_dir.mkdir(parents=True, exist_ok=True)
    rl_path = rl_dir / "rl_scripted_effects.txt"
    rl_path.write_text(rl_output, encoding='utf-8-sig')
    print(f"Random list written to: {rl_path} ({len(rl_lines)} entries)")

    # Static modifiers output — one block per advance that had modifiers removed
    static_sections = []
    for a in result.advances:
        if not a["modifiers"]:
            continue
        lines = [f"modifier_{a['_name']} = {{"]
        for key, value in a["modifiers"].items():
            if isinstance(value, list):
                for v in value:
                    lines.append(f"\t{key} = {v}")
            else:
                lines.append(f"\t{key} = {value}")
        lines.append("}")
        static_sections.append("\n".join(lines))
    static_dir = Path("../main_menu/common/static_modifiers")
    static_dir.mkdir(parents=True, exist_ok=True)
    static_path = static_dir / "rl_modifiers.txt"
    static_path.write_text("\n\n".join(static_sections), encoding='utf-8-sig')
    print(f"Static modifiers written to: {static_path} ({len(static_sections)} entries)")

    # Options output
    option_sections = []
    for i, a in enumerate(result.advances):
        name = a["_name"]
        has_modifiers = bool(a["modifiers"])
        has_unlocks = bool(set(a.keys()) & _UNLOCK_KEYS)

        if not has_modifiers and not has_unlocks:
            continue

        trigger_block = (
            f"\ttrigger = {{\n"
            f"\t\tOR = {{\n"
            f"\t\t\tvar:rl_event_1 = {i}\n"
            f"\t\t\tvar:rl_event_2 = {i}\n"
            f"\t\t\tvar:rl_event_3 = {i}\n"
            f"\t\t}}\n"
            f"\t}}"
        )

        lines = [
            "option = {",
            f"\tname = rl_events.1.{i}",
            trigger_block,
        ]

        if has_modifiers:
            # rl_grant_mod scales the modifier by the rolled rarity (size).
            lines.append(f"\trl_grant_mod = {{ m = modifier_{name} }}")

        if has_unlocks:
            lines.append("\thidden_effect = {")
            lines.append(f"\t\tset_variable = flag_dummy_{name}")
            lines.append(f"\t\tresearch_advance = advance_type:dummy_{name}")
            # No baseline adjustment needed: dummy_{name} carries no rl_roll_mtd,
            # so granting it does not change modifier:rl_roll_mtd (the detection
            # counter), and it won't be mistaken for a new player research.
            lines.append(f"\t\tset_variable = var_{name}")
            lines.append("\t}")
            lines.append("\tshow_as_tooltip = {")
            lines.append(f"\t\tcustom_tooltip = rl_tt_{name}")
            lines.append("\t}")
        else:
            # modifier-only: hidden_effect with just set_variable
            lines.append("\thidden_effect = {")
            lines.append(f"\t\tset_variable = var_{name}")
            lines.append("\t}")

        # Cursed rolls apply their drawback no matter which option is taken.
        lines.append("\trl_apply_curse = yes")
        # Synergy set: count this pick toward its theme and check set bonuses,
        # and raise escalating threat (both handled inside rl_take_<tag>).
        lines.append(f"\trl_take_{_classify_tag(a)} = yes")

        lines.append("}")
        option_sections.append("\n".join(lines))

    options_joined = "\n\n".join(option_sections)
    # Indent every line of the options block one extra tab so it nests correctly
    indented_options = "\n".join(
        f"\t{line}" if line else line
        for line in options_joined.splitlines()
    )

    # Rarity-tiered description (var:rl_rarity is set by the roll effect).
    tiered_desc = (
        "\tdesc = {\n"
        "\t\tfirst_valid = {\n"
        "\t\t\ttriggered_desc = { trigger = { var:rl_cursed = 1 } desc = rl_events.1.desc.cursed }\n"
        "\t\t\ttriggered_desc = { trigger = { var:rl_rarity = 3 } desc = rl_events.1.desc.legendary }\n"
        "\t\t\ttriggered_desc = { trigger = { var:rl_rarity = 2 } desc = rl_events.1.desc.rare }\n"
        "\t\t\ttriggered_desc = { desc = rl_events.1.desc.common }\n"
        "\t\t}\n"
        "\t}\n"
    )

    # Reroll option: replace the three offers once per roll, if enabled and
    # a reroll is still available. rl_do_reroll re-opens the event.
    # The reroll effect runs rl_rand_se (a 2566-entry random_list); if the engine
    # tries to auto-generate its outcome tooltip it enumerates every entry and
    # lags on hover. Wrap it in hidden_effect and supply a custom_tooltip.
    reroll_option = (
        "\toption = {\n"
        "\t\tname = rl_events.1.reroll\n"
        "\t\ttrigger = {\n"
        "\t\t\tvar:rl_reroll_tokens > 0\n"
        "\t\t\t\"global_variable_map(cmm|flag:europa_survivors__enable_reroll)\" >= 1\n"
        "\t\t}\n"
        "\t\tcustom_tooltip = rl_events.1.reroll.tt\n"
        "\t\thidden_effect = {\n"
        "\t\t\trl_do_reroll = yes\n"
        "\t\t}\n"
        "\t}\n"
    )

    events_output = (
        "namespace = rl_events\n"
        "\n"
        "rl_events.1 = {\n"
        "\ttype = country_event\n"
        "\ttitle = rl_events.1.title\n"
        f"{tiered_desc}"
        "\n"
        "\toutcome = positive\n"
        "\n"
        f"{indented_options}\n"
        "\n"
        f"{reroll_option}"
        "}"
    )

    events_dir = Path("../in_game/events")
    events_dir.mkdir(parents=True, exist_ok=True)
    events_path = events_dir / "rl_events.txt"
    events_path.write_text(events_output, encoding='utf-8-sig')
    print(f"Options written to: {events_path} ({len(option_sections)} entries)")

    # Localization output
    loc = parse_loc_dir(args.loc_dir) if args.loc_dir else {}
    loc_lines = ["l_english:"]

    # Modifier loc keys
    for a in result.advances:
        if not a["modifiers"]:
            continue
        name = a["_name"]
        loc_name = loc.get(name, "")
        loc_desc = loc.get(f"{name}_desc", "")
        loc_lines.append(f' STATIC_MODIFIER_NAME_modifier_{name}: "{loc_name}"')
        loc_lines.append(f' STATIC_MODIFIER_DESC_modifier_{name}: "{loc_desc}"')

    # Option name loc keys
    for i, a in enumerate(result.advances):
        name = a["_name"]
        has_modifiers = bool(a["modifiers"])
        has_unlocks = bool(set(a.keys()) & _UNLOCK_KEYS)
        if not has_modifiers and not has_unlocks:
            continue
        loc_name = loc.get(name, "")
        loc_lines.append(f' rl_events.1.{i}: "{loc_name}"')

    # Tooltip loc keys — one per unlock key per advance
    for a in result.advances:
        name = a["_name"]
        for key in _UNLOCK_KEYS:
            if key not in a:
                continue
            value = a[key]
            values = value if isinstance(value, list) else [value]
            for v in values:
                if not isinstance(v, str):
                    continue
                label = _unlock_value_to_label(v)
                tooltip_text = f"Unlocks {label}"
                loc_lines.append(f' rl_tt_{name}: "{tooltip_text}"')
                loc_lines.append(f' dummy_{name}: "{tooltip_text}"')
                loc_lines.append(f' dummy_{name}_desc: "{tooltip_text}"')

    # Deduplicate loc keys (first occurrence wins, matching game behavior) so the
    # engine doesn't warn about duplicate keys for advances with multiple unlocks.
    _loc_key_re = re.compile(r'^\s*([A-Za-z0-9_.]+):')
    seen_keys: set[str] = set()
    deduped_loc: list[str] = []
    for line in loc_lines:
        m = _loc_key_re.match(line)
        if m:
            if m.group(1) in seen_keys:
                continue
            seen_keys.add(m.group(1))
        deduped_loc.append(line)
    loc_lines = deduped_loc

    loc_dir_out = Path("../main_menu/localization/english")
    loc_dir_out.mkdir(parents=True, exist_ok=True)
    loc_path = loc_dir_out / "rl_generated_loc_l_english.yml"
    loc_path.write_text("\n".join(loc_lines) + "\n", encoding='utf-8-sig')
    print(f"Localization written to: {loc_path}")
    print("(Full data including modifiers/unlocks retained in memory only.)")


if __name__ == "__main__":
    main()