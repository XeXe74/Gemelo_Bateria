# Panel (PAN): Node-RED con Dashboard 2.0. Contexto de construcción: raíz del repositorio.
FROM nodered/node-red:4.0.9
RUN npm install --no-audit --no-fund @flowfuse/node-red-dashboard@1.30.2
COPY servicios/nodered/settings.js /usr/src/node-red/ajustes/settings.js
COPY servicios/nodered/flows_cred.json /usr/src/node-red/ajustes/flows_cred.json
