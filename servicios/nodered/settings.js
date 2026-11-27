// Configuración de Node-RED para el panel del gemelo (PAN, GDB-DIS apartado 3.8).
// Las contraseñas llegan del fichero .env como variables de entorno; aquí no se guarda ninguna.
const bcrypt = require("bcryptjs");
const fs = require("fs");

// Node-RED lee las credenciales de /data/flows_cred.json. El fichero solo contiene los nombres de las
// variables de entorno (${BUS_USUARIO} y ${BUS_CLAVE}), así que se copia desde la imagen en cada arranque.
fs.copyFileSync("/usr/src/node-red/ajustes/flows_cred.json", "/data/flows_cred.json");

const claveAdmin = process.env.PANEL_CLAVE_ADMIN || "";
const claveLector = process.env.PANEL_CLAVE_LECTOR || "";

module.exports = {
    uiPort: 1880,
    // Los flujos están en servicios/nodered/flujos, que se monta desde el repositorio, de forma
    // que los cambios hechos en el editor quedan identificados en git.
    flowFile: "flujos/flows.json",
    flowFilePretty: true,
    // Las credenciales del bus se guardan como ${BUS_USUARIO} y ${BUS_CLAVE} y se sustituyen al
    // cargar, por lo que el fichero de credenciales no necesita cifrado ni contiene secretos.
    credentialSecret: false,

    // Editor de flujos: solo el administrador (DD-07).
    adminAuth: {
        type: "credentials",
        users: function (usuario) {
            return Promise.resolve(usuario === "administrador" ? { username: usuario, permissions: "*" } : null);
        },
        authenticate: function (usuario, clave) {
            const ok = usuario === "administrador" && claveAdmin !== "" && clave === claveAdmin;
            return Promise.resolve(ok ? { username: usuario, permissions: "*" } : null);
        },
    },

    // Panel: usuario y contraseña para entrar. El acceso por rol a cada página se añadirá con el
    // panel completo (DD-07); en el gemelo mínimo hay un único usuario de consulta.
    httpNodeAuth: claveLector ? { user: "lector", pass: bcrypt.hashSync(claveLector, 8) } : undefined,

    functionGlobalContext: {},
    exportGlobalContextKeys: false,
    logging: {
        // audit: anota los inicios de sesión en el editor (DD-06).
        console: { level: "info", metrics: false, audit: true },
    },
    editorTheme: {
        projects: { enabled: false },
        tours: false,
    },
    diagnostics: { enabled: false },
    telemetry: { enabled: false },
};
