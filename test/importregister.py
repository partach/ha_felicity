from your_const import _REGISTERS

# Sort by address
sorted_regs = sorted((info["address"], key) for key, info in _REGISTERS.items())

groups = []
current_start = None
current_keys = []

for addr, key in sorted_regs:
    if current_start is None:
        current_start = addr
        current_keys = [key]
    elif addr <= current_start + len(current_keys):  # allow gap of 0 (consecutive)
        current_keys.append(key)
    else:
        # Save group
        groups.append({"start": current_start, "count": len(current_keys), "keys": current_keys})
        current_start = addr
        current_keys = [key]

# Last group
if current_start is not None:
    groups.append({"start": current_start, "count": len(current_keys), "keys": current_keys})

# Print
import json
print(json.dumps(groups, indent=2))
