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

// U16.17: the client unwraps the REST envelope — single resources are the
// object itself, collections are { items: [...], pagination?, ... }.
export interface ApiCollection<T = unknown> {
  items?: T[];
  error?: { code: string; message: string };
  pagination?: { next_cursor: string | null; has_more: boolean };
  total_count?: number;
  receipts?: Array<Record<string, unknown>>;
}

export function assertSuccess<T>(response: unknown, label: string): T {
  const r = response as { error?: { code: string; message: string } };
  if (
    r !== null &&
    typeof r === "object" &&
    "error" in r &&
    r.error &&
    typeof r.error === "object"
  ) {
    throw new Error(`${label} returned error: ${r.error.code} — ${r.error.message}`);
  }
  return response as T;
}

export function assertItems<T>(response: unknown, label: string): T[] {
  const r = assertSuccess<ApiCollection<T>>(response, label);
  if (!Array.isArray(r.items)) {
    throw new Error(`${label} missing "items" array in response`);
  }
  return r.items;
}

export function assertError(response: unknown, expectedCode: string, label: string): void {
  const r = response as { error?: { code: string; message: string } };
  if (!r.error) {
    throw new Error(`${label} expected error ${expectedCode} but got success`);
  }
  if (r.error.code !== expectedCode) {
    throw new Error(`${label} expected error ${expectedCode} but got ${r.error.code}: ${r.error.message}`);
  }
}
