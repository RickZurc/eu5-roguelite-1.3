# This script needs to do the following:
# 1. variables and automodifiers
# 2. event options
# 3. localization for event options & desc; and for auto modifiers

import pandas as pd

df = pd.read_csv('rl_input.csv')

counters = [0, 0, 0] # adm, dip, mil

auto_syntax = '''%s_auto_mod = {
    limit = { has_variable = var_%s_%s }
    scales_with = var:var_%s_%s
    %s = %s
}\n\n'''

option_syntax = '''
    option = {
		name = rl_events.1.%s.%s
        trigger = { var:rl_%s_event = { compare_value = %s } }
        hidden_effect = {
            if = {
                limit = { has_variable = var_%s_%s }
                change_variable = { name = var_%s_%s add = 1 }
            } else = {
                set_variable = { name = var_%s_%s value = 1 }
            }
        }
        show_as_tooltip = {
            add_country_modifier = { modifier = %s_fake_mod years = -1 }
        }
	}
'''

auto_mod = ""
option = ""
loc = ""
run = ""
fake_mod = ""

for item in df.values.tolist():
    type = item[2]
    if type == 'adm':
        counter = counters[0]
        counters[0] += 1
    if type == 'dip':
        counter = counters[1]
        counters[1] += 1
    if type == 'mil':
        counter = counters[2]
        counters[2] += 1
    auto_mod += auto_syntax % (item[0], item[2], counter, item[2], counter, item[0], item[1])
    option += option_syntax % (item[2], counter, item[2], counter, item[2], counter, item[2], counter, item[2], counter, item[0])
    fake_mod += "%s_fake_mod = { %s = %s }\n" % (item[0], item[0], item[1])
    run += "set_variable = { name = var_%s_%s value = 1 }\n" % (item[2], counter)
    loc += " rl_events.1.%s.%s: \"Upgrade %s\"\n" % (item[2], counter, item[3])
    loc += " AUTO_MODIFIER_NAME_%s_auto_mod: \"%s Upgrades\"\n" % (item[0], item[3])
    loc += " AUTO_MODIFIER_DESC_%s_auto_mod: \"%s Upgrades\"\n" % (item[0], item[3])
    loc += " STATIC_MODIFIER_NAME_%s_fake_mod: \"%s Upgrades (Preview)\"\n" % (item[0], item[3])
    loc += " STATIC_MODIFIER_DESC_%s_fake_mod: \"A preview of the modifier upgrade. All upgrades stack without any cap.\"\n" % (item[0])

with open("auto.txt", "w") as f:
    f.write(auto_mod)

with open("opt.txt", "w") as f:
    f.write(option)

with open("fake.txt", "w") as f:
    f.write(fake_mod)

with open("loc.txt", "w") as f:
    f.write(loc)

with open("run.txt", "w") as f:
    f.write(run)