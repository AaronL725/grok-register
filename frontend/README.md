# Grok Register WebUI

React 19, TypeScript and Vite. The FastAPI server remains the API owner and serves the compiled `dist/` when present.

```bash
cd frontend
npm install
npm run typecheck
npm test
npm run build
```

Run `npm run dev` alongside `python -m web.server` for local development. Vite proxies `/api` to `http://127.0.0.1:8092`.

The legacy Python GUI, CLI, configuration schema and existing API paths are unchanged. The old static WebUI stays as an explicit compatibility fallback when React has not yet been built.
