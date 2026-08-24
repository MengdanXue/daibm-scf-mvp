'use strict';

const DEFINITIONS = Object.freeze({
  INVALID_JSON: [400, 'Request body must be valid JSON', false],
  ANCHOR_NOT_FOUND: [404, 'Anchor does not exist', false],
  ROUTE_NOT_FOUND: [404, 'Route does not exist', false],
  METHOD_NOT_ALLOWED: [405, 'Method is not allowed for this route', false],
  ANCHOR_CONFLICT: [409, 'Anchor ID already exists with different content', false],
  PAYLOAD_TOO_LARGE: [413, 'Request body exceeds 16384 bytes', false],
  UNSUPPORTED_MEDIA_TYPE: [415, 'Content-Type must be application/json', false],
  INVALID_ANCHOR: [422, 'Anchor payload is invalid', false],
  FABRIC_UNAVAILABLE: [503, 'Fabric gateway is unavailable', true],
  FABRIC_TIMEOUT: [504, 'Fabric gateway request timed out', true],
  INTERNAL_ERROR: [500, 'Internal gateway error', true],
});

class PublicError extends Error {
  constructor(code, options = {}) {
    const definition = DEFINITIONS[code] || DEFINITIONS.INTERNAL_ERROR;
    super(options.message || definition[1]);
    this.name = 'PublicError';
    this.code = DEFINITIONS[code] ? code : 'INTERNAL_ERROR';
    this.status = definition[0];
    this.retryable = definition[2];
    this.cause = options.cause;
  }

  toJSON() {
    return { code: this.code, message: this.message, retryable: this.retryable };
  }
}

function toPublicError(error) {
  if (error instanceof PublicError) return error;
  if (error && Object.hasOwn(DEFINITIONS, error.code)) return new PublicError(error.code, { cause: error });
  return new PublicError('INTERNAL_ERROR', { cause: error });
}

module.exports = { PublicError, toPublicError };
