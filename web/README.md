# cortex web

React 18 + Vite 5 + TypeScript dashboard for the cortex server.

## Dev

```sh
export PATH=/home/wardn/.nvm/versions/node/v22.22.3/bin:$PATH  # node >= 18
npm install
npm run dev        # http://localhost:5173, proxies /api /ws /assets /health → :8642
```

## Build

```sh
npm run build      # typechecks (tsc --noEmit strict), then emits ../src/cortex/server/webdist/
npm run typecheck  # typecheck only
```

The build writes `index.html` plus hashed assets under `webdist/app/`. The backend
serves `webdist/index.html` at `/` and the assets at `/app/*`. `favicon.svg` and
`lockup-horizontal.svg` are copied verbatim into the webdist root (from `public/`).

Brand styling is vendored from `src/cortex/server/web/` into `src/styles/`
(`tokens.css`, `brand.css`); all custom CSS goes through `--ul-*` custom properties.
