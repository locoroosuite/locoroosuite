import { z } from "zod";
function json(data) {
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}
export function registerCalendarTools(server, client) {
    server.tool("calendar_list_calendars", "List available calendars with name, color, and default status", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
    }, async ({ account_id }) => {
        const data = await client.get("/api/v1/calendar/calendars", client.accountId(account_id));
        return json(data);
    });
    server.tool("calendar_create_calendar", "Create a new calendar", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        name: z.string().describe("Calendar name"),
        color: z.string().optional().describe("Calendar color as hex (e.g. #3a87ad)"),
    }, async ({ account_id, name, color }) => {
        const data = await client.post("/api/v1/calendar/calendars", {
            ...client.accountId(account_id),
            name,
            color,
        });
        return json(data);
    });
    server.tool("calendar_update_calendar", "Update calendar name or color", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        calendar_id: z.string().describe("Calendar ID"),
        name: z.string().optional().describe("New calendar name"),
        color: z.string().optional().describe("New calendar color as hex"),
    }, async ({ account_id, calendar_id, name, color }) => {
        const body = { ...client.accountId(account_id) };
        if (name !== undefined)
            body.name = name;
        if (color !== undefined)
            body.color = color;
        const data = await client.put(`/api/v1/calendar/calendars/${encodeURIComponent(calendar_id)}`, body);
        return json(data);
    });
    server.tool("calendar_delete_calendar", "Delete a calendar and all its events", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        calendar_id: z.string().describe("Calendar ID"),
    }, async ({ account_id, calendar_id }) => {
        const data = await client.delete(`/api/v1/calendar/calendars/${encodeURIComponent(calendar_id)}`, { ...client.accountId(account_id), confirm: true });
        return json(data);
    });
    server.tool("calendar_list_events", "List events in a calendar with date range", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        calendar_id: z.string().describe("Calendar ID"),
        since: z.string().optional().describe("ISO 8601 datetime — start of range"),
        until: z.string().optional().describe("ISO 8601 datetime — end of range"),
        search: z.string().optional().describe("Search events by summary"),
        cursor: z.string().optional().describe("Pagination cursor from previous response"),
        max_results: z.number().min(1).max(200).optional().describe("Maximum number of events to return (1–200, default 50)"),
    }, async ({ account_id, calendar_id, since, until, search, cursor, max_results }) => {
        const data = await client.get(`/api/v1/calendar/calendars/${encodeURIComponent(calendar_id)}/events`, {
            ...client.accountId(account_id),
            since,
            until,
            search,
            cursor,
            max_results: max_results?.toString(),
        });
        return json(data);
    });
    server.tool("calendar_get_event", "Get full event detail including attendees, recurrence, and reminders", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        event_id: z.string().describe("Event ID"),
    }, async ({ account_id, event_id }) => {
        const data = await client.get(`/api/v1/calendar/events/${encodeURIComponent(event_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("calendar_search_events", "Search events by summary across calendars", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        q: z.string().describe("Search query"),
        since: z.string().optional().describe("ISO 8601 datetime — start of range"),
        until: z.string().optional().describe("ISO 8601 datetime — end of range"),
        cursor: z.string().optional().describe("Pagination cursor from previous response"),
        max_results: z.number().min(1).max(200).optional().describe("Maximum number of results to return (1–200, default 50)"),
    }, async ({ account_id, q, since, until, cursor, max_results }) => {
        const data = await client.get("/api/v1/calendar/search", {
            ...client.accountId(account_id),
            q,
            since,
            until,
            cursor,
            max_results: max_results?.toString(),
        });
        return json(data);
    });
    server.tool("calendar_create_event", "Create a new event", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        calendar_id: z.string().describe("Calendar to create the event in"),
        summary: z.string().describe("Event title/summary"),
        description: z.string().optional().describe("Event description"),
        location: z.string().optional().describe("Event location"),
        start: z.string().describe("Start time as ISO 8601 with timezone"),
        end: z.string().describe("End time as ISO 8601 with timezone"),
        is_all_day: z.boolean().optional().describe("Whether this is an all-day event"),
        attendees: z.array(z.object({
            email: z.string(),
            name: z.string().optional(),
            role: z.string().optional(),
        })).optional().describe("Attendees"),
        reminders: z.array(z.object({
            type: z.string(),
            trigger_minutes: z.number(),
        })).optional().describe("Reminders"),
        recurrence: z.string().optional().describe("RRULE string for recurrence"),
    }, async ({ account_id, calendar_id, summary, description, location, start, end, is_all_day, attendees, reminders, recurrence }) => {
        const data = await client.post("/api/v1/calendar/events", {
            ...client.accountId(account_id),
            calendar_id,
            summary,
            description,
            location,
            start,
            end,
            is_all_day,
            attendees,
            reminders,
            recurrence,
        });
        return json(data);
    });
    server.tool("calendar_update_event", "Update an existing event", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        event_id: z.string().describe("Event ID"),
        summary: z.string().optional().describe("Event title/summary"),
        description: z.string().optional().describe("Event description"),
        location: z.string().optional().describe("Event location"),
        start: z.string().optional().describe("Start time as ISO 8601 with timezone"),
        end: z.string().optional().describe("End time as ISO 8601 with timezone"),
        is_all_day: z.boolean().optional().describe("Whether this is an all-day event"),
        calendar_id: z.string().optional().describe("Move event to a different calendar"),
        attendees: z.array(z.object({
            email: z.string(),
            name: z.string().optional(),
            role: z.string().optional(),
        })).optional().describe("Attendees (replaces existing)"),
        reminders: z.array(z.object({
            type: z.string(),
            trigger_minutes: z.number(),
        })).optional().describe("Reminders (replaces existing)"),
        recurrence: z.string().optional().describe("RRULE string for recurrence"),
        scope: z.enum(["instance", "future", "series"]).optional().describe("For recurring events: which occurrences to update"),
    }, async ({ account_id, event_id, summary, description, location, start, end, is_all_day, calendar_id, attendees, reminders, recurrence, scope }) => {
        const body = { ...client.accountId(account_id) };
        if (summary !== undefined)
            body.summary = summary;
        if (description !== undefined)
            body.description = description;
        if (location !== undefined)
            body.location = location;
        if (start !== undefined)
            body.start = start;
        if (end !== undefined)
            body.end = end;
        if (is_all_day !== undefined)
            body.is_all_day = is_all_day;
        if (calendar_id !== undefined)
            body.calendar_id = calendar_id;
        if (attendees !== undefined)
            body.attendees = attendees;
        if (reminders !== undefined)
            body.reminders = reminders;
        if (recurrence !== undefined)
            body.recurrence = recurrence;
        if (scope !== undefined)
            body.scope = scope;
        const data = await client.put(`/api/v1/calendar/events/${encodeURIComponent(event_id)}`, body);
        return json(data);
    });
    server.tool("calendar_delete_event", "Delete an event", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        event_id: z.string().describe("Event ID"),
        scope: z.enum(["instance", "future", "series"]).optional().describe("For recurring events: which occurrences to delete"),
    }, async ({ account_id, event_id, scope }) => {
        const body = { ...client.accountId(account_id) };
        if (scope !== undefined)
            body.scope = scope;
        const data = await client.delete(`/api/v1/calendar/events/${encodeURIComponent(event_id)}`, body);
        return json(data);
    });
    server.tool("calendar_check_free_busy", "Check free/busy time ranges for calendars", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        calendar_ids: z.array(z.string()).describe("Calendar IDs to check"),
        start: z.string().describe("Range start as ISO 8601"),
        end: z.string().describe("Range end as ISO 8601"),
    }, async ({ account_id, calendar_ids, start, end }) => {
        const data = await client.post("/api/v1/calendar/free-busy", {
            ...client.accountId(account_id),
            calendar_ids,
            start,
            end,
        });
        return json(data);
    });
}
//# sourceMappingURL=calendar.js.map