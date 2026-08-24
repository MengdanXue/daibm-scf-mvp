'use strict';

const http = require('node:http');

const { isCanonicalUuid } = require('./anchor');
const { AnchorService } = require('./anchor-service');
const { PublicError, toPublicError } = require('./errors');

const MAX_BODY_BYTES = 16 * 1024;

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

async function readJson(request) {
  const contentType = request.headers['content-type'] || '';
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) throw new PublicError('UNSUPPORTED_MEDIA_TYPE');
  const declaredLength = Number(request.headers['content-length']);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) throw new PublicError('PAYLOAD_TOO_LARGE');

  const chunks = [];
  let total = 0;
  let tooLarge = false;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) {
      tooLarge = true;
    } else {
      chunks.push(chunk);
    }
  }
  if (tooLarge) throw new PublicError('PAYLOAD_TOO_LARGE');
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch (error) {
    throw new PublicError('INVALID_JSON', { cause: error });
  }
}

function parseAnchorPath(rawUrl) {
  if (rawUrl.includes('?')) return null;
  const match = /^\/anchors\/([^/]+)$/.exec(rawUrl);
  if (!match) return null;
  let anchorId;
  try {
    anchorId = decodeURIComponent(match[1]);
  } catch (error) {
    throw new PublicError('INVALID_ANCHOR', { cause: error });
  }
  if (!isCanonicalUuid(anchorId)) throw new PublicError('INVALID_ANCHOR');
  return anchorId;
}

function createGatewayServer({ fabric, logger = null }) {
  const service = new AnchorService(fabric);
  const server = http.createServer(async (request, response) => {
    try {
      if (request.url === '/health') {
        if (request.method !== 'GET') throw new PublicError('METHOD_NOT_ALLOWED');
        return sendJson(response, 200, await service.health());
      }
      if (request.url === '/anchors') {
        if (request.method !== 'POST') throw new PublicError('METHOD_NOT_ALLOWED');
        const result = await service.create(await readJson(request));
        return sendJson(response, result.created ? 201 : 200, result.anchor);
      }
      const anchorId = parseAnchorPath(request.url);
      if (anchorId !== null) {
        if (request.method !== 'GET') throw new PublicError('METHOD_NOT_ALLOWED');
        return sendJson(response, 200, await service.read(anchorId));
      }
      throw new PublicError('ROUTE_NOT_FOUND');
    } catch (error) {
      const publicError = toPublicError(error);
      if (logger && typeof logger.error === 'function') {
        logger.error({ code: publicError.code, status: publicError.status }, 'gateway request failed');
      }
      if (!response.headersSent) sendJson(response, publicError.status, publicError.toJSON());
      else response.destroy();
    }
  });
  server.headersTimeout = 5_000;
  server.requestTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  return server;
}

module.exports = { createGatewayServer };
