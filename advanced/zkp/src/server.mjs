import http from 'node:http';
import { randomBytes } from 'node:crypto';

import { proveInvoiceLimit } from '../index.mjs';

const MAX_BODY_BYTES = 4 * 1024;
const UINT64_MAX = (2n ** 64n) - 1n;
const BN128_SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
export const CIRCUIT_VERSION = 'invoice_limit@1';

function sendJson(response, status, body) {
  const encoded = Buffer.from(JSON.stringify(body), 'utf8');
  response.writeHead(status, {
    'cache-control': 'no-store',
    'content-length': encoded.length,
    'content-type': 'application/json; charset=utf-8',
    'x-content-type-options': 'nosniff',
  });
  response.end(encoded);
}

class PublicError extends Error {
  constructor(code, status) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

async function readJson(request) {
  const contentType = request.headers['content-type'] || '';
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) throw new PublicError('UNSUPPORTED_MEDIA_TYPE', 415);
  const declaredLength = Number(request.headers['content-length']);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) throw new PublicError('PAYLOAD_TOO_LARGE', 413);
  const chunks = [];
  let total = 0;
  let tooLarge = false;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) tooLarge = true;
    else chunks.push(chunk);
  }
  if (tooLarge) throw new PublicError('PAYLOAD_TOO_LARGE', 413);
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch (error) {
    throw new PublicError('INVALID_JSON', 400);
  }
}

// Decimal strings only. Accepting numbers would silently round any amount
// above 2^53 minor units, and the circuit's whole point is an exact bound.
function parseUint64(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(value)) return null;
  const parsed = BigInt(value);
  return parsed >= 0n && parsed <= UINT64_MAX ? parsed : null;
}

// The salt hides the amount behind the Poseidon commitment. It is drawn per
// request and never returned or persisted: this demonstration binds the
// amount, it does not later re-open the commitment.
function drawSalt() {
  while (true) {
    const candidate = BigInt(`0x${randomBytes(32).toString('hex')}`);
    if (candidate < BN128_SCALAR_FIELD) return candidate;
  }
}

export async function proveRequest(body) {
  const invoiceAmount = parseUint64(body?.invoiceAmount);
  const financingLimit = parseUint64(body?.financingLimit);
  if (invoiceAmount === null || financingLimit === null) throw new PublicError('INVALID_STATEMENT', 400);
  if (invoiceAmount === 0n || financingLimit === 0n) throw new PublicError('INVALID_STATEMENT', 400);
  if (invoiceAmount > financingLimit) throw new PublicError('STATEMENT_FALSE', 422);
  const { proof, publicSignals } = await proveInvoiceLimit({
    invoiceAmount,
    financingLimit,
    salt: drawSalt(),
  });
  return { circuitVersion: CIRCUIT_VERSION, proof, publicSignals };
}

export function createProverServer({ logger = null } = {}) {
  const server = http.createServer(async (request, response) => {
    try {
      if (request.url === '/health') {
        if (request.method !== 'GET') throw new PublicError('METHOD_NOT_ALLOWED', 405);
        return sendJson(response, 200, { status: 'ready', circuitVersion: CIRCUIT_VERSION });
      }
      if (request.url === '/proofs/invoice-limit') {
        if (request.method !== 'POST') throw new PublicError('METHOD_NOT_ALLOWED', 405);
        return sendJson(response, 200, await proveRequest(await readJson(request)));
      }
      throw new PublicError('ROUTE_NOT_FOUND', 404);
    } catch (error) {
      const code = error instanceof PublicError ? error.code : 'PROVER_FAILED';
      const status = error instanceof PublicError ? error.status : 500;
      if (logger && typeof logger.error === 'function') logger.error({ code, status }, 'prover request failed');
      if (!response.headersSent) sendJson(response, status, { code });
      else response.destroy();
    }
  });
  server.headersTimeout = 5_000;
  // Groth16 proving dominates this budget; the client times out at 10s.
  server.requestTimeout = 30_000;
  server.keepAliveTimeout = 5_000;
  return server;
}
