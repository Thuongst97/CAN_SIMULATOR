# AutoSim Studio (ICAN) 1.2.4

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.4_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, rules, Test APIs and trace filters.

## Improved

- **Text sizes follow a clear order.** Before, the panel titles, the header / toolbar buttons and the column
  headers were smaller (12 px) than the rows (13 px). Now:

  | Element | Size |
  |---|---|
  | Panel titles (*Bus trace*, *Test API*, *Interactive generator*…) | 15 px |
  | Column headers | 13 px, semibold, full colour (no longer grey) |
  | Buttons, fields and lists in headers and toolbars | 13 px, like the rows |
  | Rows | 13 px |

  This applies to the Bus trace, Test API, Simulation, the Database, and the status badges.
- **Columns fit their headers.** *Cycle [ms]* in the trace and *Value on the bus* in Test API are no longer cut.
- **Simulation gives the Interactive generator more room** by default: 62 % of the width, against 50 % before.
  Message names, the *Symbolic* column and *Add signals…* now show in full. The splitter still moves, and a width
  you set is kept.

See [1.2.3](RELEASE_NOTES_1.2.3.md) for the trace click, menu and remove-message fixes.
