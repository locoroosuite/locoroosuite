import { z } from "zod";
function json(data) {
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}
export function registerChatTools(server, client) {
    server.registerTool("chat_list_rooms", { description: "List chat rooms and direct messages", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        }) }, async ({ account_id }) => {
        const data = await client.get("/api/v1/chat/rooms", client.accountId(account_id));
        return json(data);
    });
    server.registerTool("chat_create_room", { description: "Create a chat room (or a direct message with is_direct=true and invite)", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            name: z.string().optional().describe("Room name (not required for direct messages)"),
            topic: z.string().optional().describe("Room topic"),
            is_public: z.boolean().optional().describe("Whether the room is public"),
            is_direct: z.boolean().optional().describe("Create a direct message room"),
            invite: z.array(z.string()).optional().describe("Matrix user IDs to invite"),
        }) }, async ({ account_id, name, topic, is_public, is_direct, invite }) => {
        const body = { ...client.accountId(account_id) };
        if (name !== undefined)
            body.name = name;
        if (topic !== undefined)
            body.topic = topic;
        if (is_public !== undefined)
            body.is_public = is_public;
        if (is_direct !== undefined)
            body.is_direct = is_direct;
        if (invite !== undefined)
            body.invite = invite;
        const data = await client.post("/api/v1/chat/rooms", body);
        return json(data);
    });
    server.registerTool("chat_list_messages", { description: "List messages in a chat room", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID"),
            limit: z.number().int().min(1).max(200).optional().describe("Max messages to return"),
        }) }, async ({ account_id, room_id, limit }) => {
        const params = {
            ...client.accountId(account_id),
        };
        if (limit !== undefined)
            params.limit = String(limit);
        const data = await client.get(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/messages`, params);
        return json(data);
    });
    server.registerTool("chat_send_message", { description: "Send a text message to a chat room", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID"),
            body: z.string().describe("Message text"),
        }) }, async ({ account_id, room_id, body }) => {
        const data = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/messages`, {
            ...client.accountId(account_id),
            body,
        });
        return json(data);
    });
    server.registerTool("chat_edit_message", { description: "Edit one of your own chat messages", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            event_id: z.string().describe("Matrix event ID of the message"),
            body: z.string().describe("New message text"),
        }) }, async ({ account_id, event_id, body }) => {
        const data = await client.post(`/api/v1/chat/messages/${encodeURIComponent(event_id)}/edit`, { ...client.accountId(account_id), body });
        return json(data);
    });
    server.registerTool("chat_delete_message", { description: "Delete (redact) a chat message", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            event_id: z.string().describe("Matrix event ID of the message"),
        }) }, async ({ account_id, event_id }) => {
        const data = await client.post(`/api/v1/chat/messages/${encodeURIComponent(event_id)}/redact`, client.accountId(account_id));
        return json(data);
    });
    server.registerTool("chat_react", { description: "Add an emoji reaction to a chat message", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            event_id: z.string().describe("Matrix event ID of the message"),
            key: z.string().describe("Reaction key (emoji)"),
        }) }, async ({ account_id, event_id, key }) => {
        const data = await client.post(`/api/v1/chat/messages/${encodeURIComponent(event_id)}/react`, { ...client.accountId(account_id), key });
        return json(data);
    });
    server.registerTool("chat_mark_room_read", { description: "Mark a chat room read up to the given event", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID"),
            event_id: z.string().describe("Event ID to mark read up to"),
        }) }, async ({ account_id, room_id, event_id }) => {
        const data = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/read`, {
            ...client.accountId(account_id),
            event_id,
        });
        return json(data);
    });
    server.registerTool("chat_invite_user", { description: "Invite a Matrix user to a chat room", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID"),
            user_id: z.string().describe("Matrix user ID to invite"),
        }) }, async ({ account_id, room_id, user_id }) => {
        const data = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/invite`, {
            ...client.accountId(account_id),
            user_id,
        });
        return json(data);
    });
    server.registerTool("chat_join_room", { description: "Join a chat room by ID or alias", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID or alias"),
        }) }, async ({ account_id, room_id }) => {
        const data = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/join`, client.accountId(account_id));
        return json(data);
    });
    server.registerTool("chat_leave_room", { description: "Leave a chat room", inputSchema: z.object({
            account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            room_id: z.string().describe("Matrix room ID"),
        }) }, async ({ account_id, room_id }) => {
        const data = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(room_id)}/leave`, client.accountId(account_id));
        return json(data);
    });
}
//# sourceMappingURL=chat.js.map