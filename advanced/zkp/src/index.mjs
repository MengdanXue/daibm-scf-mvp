import { createProverServer } from './server.mjs';

const host = process.env.PROVER_HOST || '0.0.0.0';
const port = Number(process.env.PROVER_PORT || 8091);

const server = createProverServer({ logger: console });
server.listen(port, host, () => {
  console.log(JSON.stringify({ event: 'prover_listening', host, port }));
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
