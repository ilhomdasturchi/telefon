Phone icon on the left activity bar. Click it to speak a message or ring the local call API.

After an agent task ends, Telefon waits `idleSeconds`, then rings/announces **only if you are not busy in Cursor**.

Busy = Cursor window focused **and** you recently used keyboard/mouse in it (editor, chat, terminal, UI).

| Where | What you do | Call? |
|-------|-------------|-------|
| Inside Cursor | Doing something | No |
| Inside Cursor | Staring / idle | Yes |
| Outside Cursor | Anything | Yes |
