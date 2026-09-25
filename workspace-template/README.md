# collabm workspace

Default working folder for the `collabm` terminal chat (see `win\README.md` in the colabmwindow repo). Created from `workspace-template\` on first run.

| path | what |
|---|---|
| `COLLABM.md` | project instructions, sent as part of the system prompt in every chat here |
| `conversations\` | every chat, saved as `<timestamp>.md` (readable) + `<timestamp>.json` (resumable) |
| `.collabm\history.txt` | your input history (up-arrow) |

Put files you want to discuss here and reference them with `@path` in the chat;
`/run <command>` also runs in this folder.

```powershell
collabm            # open the chat here (starts the A100 first if needed)
collabm -c         # continue the most recent conversation
collabm --here     # use the current directory as the workspace instead
```
