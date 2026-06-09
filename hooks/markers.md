## Not-Happy-Jan — task-status voice markers

Not-Happy-Jan gives you a voice. When you finish a turn, signal its outcome by
emitting **one** marker on its own line in your final message. A Stop hook reads
the marker and speaks it in-character — **do not call any tool to do this**, just
write the marker. The text after the `|` is what the character says, so make it a
short, natural sentence.

- Success / task done → `[Jan:ok|All sorted, tests pass.]`
- Made progress (mid-task) → `[Jan:step|Still working through it.]`
- Milestone / a win → `[Jan:celebrate|Shipped it!]`
- Warning / heads-up → `[Bazza:warn|Heads up, this looks off.]`
- Needs your attention / blocked → `[Karren:attn|Need a decision from you.]`
- Error / failure → `[Karren:err|Not happy, Jan — the build's broken.]`

Routing: ok/step/celebrate → **Jan** (reception), warn → **Bazza** (middle
management), err/attn → **Karren** (the manager).

Use it **sparingly and honestly** — one marker per turn that reflects the real
outcome (or none for a purely conversational reply), never one on every message.
