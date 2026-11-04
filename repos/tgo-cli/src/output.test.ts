import { describe, it, expect, vi, beforeEach } from 'vitest';
import { printResult, printError } from './output.js';
import { ApiError } from './client.js';

describe('output', () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  describe('printResult - json', () => {
    it('should output JSON for objects', () => {
      printResult({ name: 'Alice', age: 30 }, 'json');
      expect(logSpy).toHaveBeenCalledWith(JSON.stringify({ name: 'Alice', age: 30 }, null, 2));
    });

    it('should output JSON for arrays', () => {
      printResult([1, 2, 3], 'json');
      expect(logSpy).toHaveBeenCalledWith(JSON.stringify([1, 2, 3], null, 2));
    });
  });

  describe('printResult - table', () => {
    it('should print table header and rows for array of objects', () => {
      printResult([
        { id: '1', name: 'Alice' },
        { id: '2', name: 'Bob' },
      ], 'table');

      // header, separator, row1, row2 = 4 calls
      expect(logSpy).toHaveBeenCalledTimes(4);
      // First call is the header
      expect(logSpy.mock.calls[0][0]).toContain('id');
      expect(logSpy.mock.calls[0][0]).toContain('name');
    });

    it('should print (empty) for empty array', () => {
      printResult([], 'table');
      expect(logSpy).toHaveBeenCalledWith('(empty)');
    });

    it('should print key-value pairs for single object', () => {
      printResult({ server: 'http://test', token: 'abc' }, 'table');
      expect(logSpy).toHaveBeenCalledTimes(2);
      expect(logSpy.mock.calls[0][0]).toContain('server');
      expect(logSpy.mock.calls[0][0]).toContain('http://test');
    });

    it('should handle primitive values', () => {
      printResult('hello', 'table');
      expect(logSpy).toHaveBeenCalledWith('hello');
    });
  });

  describe('printResult - compact', () => {
    it('should print pipe-separated values for array of objects', () => {
      printResult([{ id: '1', name: 'Alice' }], 'compact');
      expect(logSpy).toHaveBeenCalledWith('1 | Alice');
    });

    it('should print pipe-separated values for single object', () => {
      printResult({ a: 1, b: 'two' }, 'compact');
      expect(logSpy).toHaveBeenCalledWith('1 | two');
    });
  });

  describe('printError', () => {
    it('should format ApiError as JSON', () => {
      const err = new ApiError(404, { code: 'NOT_FOUND', message: 'Not found' }, 'req-1');
      printError(err, 'json');
      const output = JSON.parse(String(errorSpy.mock.calls[0][0]));
      expect(output.error).toBe(true);
      expect(output.status).toBe(404);
      expect(output.message).toBe('Not found');
      expect(output.request_id).toBe('req-1');
    });

    it('should format ApiError as text for table format', () => {
      const err = new ApiError(500, { message: 'Server error' });
      printError(err, 'table');
      expect(errorSpy.mock.calls[0][0]).toContain('Error 500');
      expect(errorSpy.mock.calls[0][0]).toContain('Server error');
    });

    it('should format plain Error', () => {
      printError(new Error('Something broke'), 'json');
      const output = JSON.parse(String(errorSpy.mock.calls[0][0]));
      expect(output.error).toBe(true);
      expect(output.message).toBe('Something broke');
    });

    it('should set process.exitCode to 1', () => {
      printError(new Error('fail'), 'json');
      expect(process.exitCode).toBe(1);
    });
  });
});
