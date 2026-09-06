# The PULSAR console

Vite + React + TypeScript. The build output is `../static`, which is what
`webapp/server.py` serves and what the wheel ships, so a rebuild must be
committed along with the source that produced it.

```
npm install
npm run dev        # localhost:5173, /api proxied to the console on 8787
npm run build      # writes ../static
npx tsc --noEmit   # types only
```

Run `pulsar dashboard` in another terminal while `npm run dev` is up.

## Layout

| Path | What lives there |
| --- | --- |
| `src/api/` | Payload types and the React Query hooks. The only place a URL is written. |
| `src/lib/` | Formatting, sorting, and the URL-parameter hook every screen's state lives in. |
| `src/components/` | Table, chart and record primitives shared by every screen. |
| `src/components/Rail.tsx` | The stack that opens a record over the screen you are on. |
| `src/shell/` | Navigation, page header, theme, command palette, toast. |
| `src/views/` | One screen each. `views/network/` holds the force simulation. |

## Two rules the layering exists to enforce

**A record is built once.** A supervisor reached from a ranking, from the map,
or from a co-authorship edge is the same `ProfessorRecord` with the same tabs. A
screen that needs something only it can produce — the graph's neighbourhood —
passes an `extraTabs` entry rather than forking the record.

**Going deeper is not navigation.** Clicking a plan, a supervisor or a message
pushes onto the rail; it does not route. Routing would discard the filters, the
scroll position, the map's camera and the layout the graph spent three seconds
converging into. The handful of links that genuinely do leave a screen are
`LeaveLink`, drawn differently, and say so.

## The simulation

`views/network/simulation.ts` is deliberately outside React. Positions change
sixty times a second, which is the one kind of state React should not own; the
component subscribes to a frame counter through `useSyncExternalStore`, so it
re-renders while the layout is warm and not at all once it settles (about 240
frames, four seconds). Switching the question hands new ties to the *running*
simulation instead of rebuilding it, so positions, pins and the camera survive.
