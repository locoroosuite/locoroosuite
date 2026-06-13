export class ApiClient {
    baseUrl;
    token;
    defaultAccountId;
    constructor(baseUrl, token, defaultAccountId) {
        this.baseUrl = baseUrl;
        this.token = token;
        this.defaultAccountId = defaultAccountId;
    }
    async request(method, path, options) {
        const url = new URL(`${this.baseUrl}${path}`);
        if (options?.params) {
            for (const [key, value] of Object.entries(options.params)) {
                if (value === undefined)
                    continue;
                const values = Array.isArray(value) ? value : [value];
                for (const v of values) {
                    url.searchParams.append(key, v);
                }
            }
        }
        const headers = {
            Authorization: `Bearer ${this.token}`,
            Accept: "application/json",
        };
        let body;
        if (options?.body !== undefined) {
            headers["Content-Type"] = "application/json";
            body = JSON.stringify(options.body);
        }
        let response;
        try {
            response = await fetch(url.toString(), { method, headers, body });
        }
        catch (err) {
            throw new ApiError(0, "CONNECTION_ERROR", `Failed to connect to ${this.baseUrl}: ${err instanceof Error ? err.message : String(err)}`);
        }
        if (response.status === 204) {
            return null;
        }
        const text = await response.text();
        let json;
        try {
            json = JSON.parse(text);
        }
        catch {
            throw new ApiError(response.status, "INVALID_RESPONSE", text);
        }
        if (!response.ok) {
            const errBody = json;
            const code = errBody.error?.code || `HTTP_${response.status}`;
            const message = errBody.error?.message || response.statusText;
            if (response.status === 403) {
                throw new ApiError(response.status, code, `This action requires additional API permissions. ${message}`);
            }
            if (response.status === 429) {
                const retryAfter = response.headers.get("Retry-After");
                throw new ApiError(response.status, code, `Rate limited.${retryAfter ? ` Retry after ${retryAfter} seconds.` : ""} ${message}`);
            }
            throw new ApiError(response.status, code, message);
        }
        return json;
    }
    async get(path, params) {
        return this.request("GET", path, { params });
    }
    async post(path, body) {
        return this.request("POST", path, { body });
    }
    async put(path, body) {
        return this.request("PUT", path, { body });
    }
    async patch(path, body) {
        return this.request("PATCH", path, { body });
    }
    async delete(path, body) {
        return this.request("DELETE", path, { body });
    }
    accountId(override) {
        const id = override || this.defaultAccountId;
        if (!id)
            return {};
        return { account_id: id };
    }
    accountIdParam(override) {
        const id = override || this.defaultAccountId;
        if (!id)
            return "";
        return `?account_id=${encodeURIComponent(id)}`;
    }
}
export class ApiError extends Error {
    status;
    code;
    constructor(status, code, message) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.code = code;
    }
}
//# sourceMappingURL=client.js.map