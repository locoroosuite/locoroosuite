export declare class ApiClient {
    private baseUrl;
    private token;
    private defaultAccountId;
    constructor(baseUrl: string, token: string, defaultAccountId: string | null);
    private request;
    get(path: string, params?: Record<string, string | string[] | undefined>): Promise<unknown>;
    post(path: string, body?: unknown): Promise<unknown>;
    put(path: string, body?: unknown): Promise<unknown>;
    patch(path: string, body?: unknown): Promise<unknown>;
    delete(path: string, body?: unknown): Promise<unknown>;
    accountId(override?: string): Record<string, string> | {};
    accountIdParam(override?: string): string;
}
export declare class ApiError extends Error {
    status: number;
    code: string;
    constructor(status: number, code: string, message: string);
}
