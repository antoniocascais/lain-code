const { test } = require('node:test');
const assert = require('node:assert');
const { shortModelName } = require('./format.js');

test('keeps full family+version (no opus-4-8 -> opus-4 collision)', () => {
  assert.equal(shortModelName('claude-opus-4-8'), 'opus-4-8');
  assert.equal(shortModelName('claude-opus-4'), 'opus-4');
  assert.notEqual(shortModelName('claude-opus-4-8'), shortModelName('claude-opus-4'));
  assert.equal(shortModelName('claude-fable-5'), 'fable-5');
  assert.equal(shortModelName('claude-sonnet-4-6'), 'sonnet-4-6');
});

test('strips [1m] context suffix and dated snapshot', () => {
  assert.equal(shortModelName('claude-opus-4-8[1m]'), 'opus-4-8');
  assert.equal(shortModelName('claude-opus-4-5-20251101'), 'opus-4-5');
  assert.equal(shortModelName('claude-sonnet-4-20250514'), 'sonnet-4');
});
