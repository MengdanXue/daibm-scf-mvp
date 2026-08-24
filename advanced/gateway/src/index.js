'use strict';

const { loadConfig } = require('./config');
const { createFabricConnection } = require('./fabric');
const { createGatewayServer } = require('./server');

async function main() {
  const config = loadConfig();
  const connection = await createFabricConnection(config);
  const server = createGatewayServer({ fabric: connection.fabric, logger: console });
  let closing = false;

  const close = () => {
    if (closing) return;
    closing = true;
    server.close(() => {
      connection.close();
      process.exitCode = 0;
    });
  };
  process.once('SIGINT', close);
  process.once('SIGTERM', close);
  server.once('error', () => {
    connection.close();
    process.exitCode = 1;
  });
  server.listen(config.port, config.host, () => {
    console.log(`Fabric anchor gateway listening internally on port ${config.port}`);
  });
}

main().catch(() => {
  console.error('Fabric anchor gateway failed to start');
  process.exitCode = 1;
});
