# Grok Register React WebUI

The local control plane uses React 19, TypeScript and Vite. FastAPI remains the API owner and serves the compiled frontend from frontend/dist when it exists. If the frontend has not been built, the existing static WebUI remains available as a compatibility fallback.

## Build

~~~bash
cd frontend
npm install
npm run typecheck
npm test
npm run build
~~~

Start the local API server as usual:

~~~bash
python -m web.server
~~~

Then open http://127.0.0.1:8092. The server automatically serves the React build after npm run build.

## Development

Run the API server and Vite in separate terminals:

~~~bash
python -m web.server
~~~

~~~bash
cd frontend
npm install
npm run dev
~~~

Vite serves the frontend at http://127.0.0.1:5173 and proxies API requests to http://127.0.0.1:8092.

The React frontend preserves the existing configuration, registration control, dependency checks, proxy pool and Outlook mailbox workflows. The Python GUI, CLI, configuration schema and API paths remain unchanged. Account management and reauthentication screens are implemented in the following planned UI stage.
