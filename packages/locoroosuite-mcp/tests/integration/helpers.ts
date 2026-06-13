import { ApiClient } from "../../src/client.js";

const BASE_URL = process.env.E2E_APP_URL || "http://localhost:8001";
const API_TOKEN = process.env.LOCOROO_API_TOKEN || "lr_FGna-L2yYnlF6WilzbyZZc8bnPmoXk38Rn00MGS5Gnw";

let _serverAvailable: boolean | null = null;

export async function isServerAvailable(): Promise<boolean> {
  if (_serverAvailable !== null) return _serverAvailable;
  try {
    const res = await fetch(`${BASE_URL}/api/v1/accounts`, {
      headers: { Authorization: `Bearer ${API_TOKEN}` },
      signal: AbortSignal.timeout(3000),
    });
    _serverAvailable = res.ok;
  } catch {
    _serverAvailable = false;
  }
  return _serverAvailable;
}

export function createClient(accountId?: string): ApiClient {
  return new ApiClient(BASE_URL, API_TOKEN, accountId || null);
}

export interface ApiResponse<T = unknown> {
  data?: T;
  error?: { code: string; message: string };
  pagination?: { next_cursor: string | null; has_more: boolean };
}

export function assertSuccess<T>(response: unknown, label: string): ApiResponse<T> {
  const r = response as ApiResponse<T>;
  if (r.error) {
    throw new Error(`${label} returned error: ${r.error.code} — ${r.error.message}`);
  }
  if (!Object.prototype.hasOwnProperty.call(r, "data")) {
    throw new Error(`${label} missing "data" key in response`);
  }
  return r;
}

export function assertError(response: unknown, expectedCode: string, label: string): void {
  const r = response as ApiResponse;
  if (!r.error) {
    throw new Error(`${label} expected error ${expectedCode} but got success`);
  }
  if (r.error.code !== expectedCode) {
    throw new Error(`${label} expected error ${expectedCode} but got ${r.error.code}: ${r.error.message}`);
  }
}
