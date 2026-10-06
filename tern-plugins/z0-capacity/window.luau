--!strict
-- Read-only Tern -> z0 host/session projection.
--
-- This plugin never switches hosts, opens/closes sessions, types into panes, or
-- dispatches work. It only snapshots cx.hosts:list() + cx.sessions:list() to a
-- bounded local JSON file that z0intelligence can consume.

local home = tern.getenv("HOME")
local output = tern.getenv("Z0INT_TERN_CAPACITY_SNAPSHOT")

if output == nil or output == "" then
    if home ~= nil and home ~= "" then
        output = home .. "/.z0int/state/tern_capacity.json"
    else
        output = tern.plugin.data .. "/tern_capacity.json"
    end
end

local parent = string.match(output, "^(.*)/[^/]+$")
if parent ~= nil and parent ~= "" then
    local ok, err = pcall(function()
        tern.fs.mkdir(parent :: string)
    end)
    if not ok then
        tern.log.warn("could not create z0 capacity export directory", err)
    end
end

local writing = false

local function present(value: any): any?
    if value == nil or value == tern.json.null then
        return nil
    end
    return value
end

local function host_id(h: any): string
    local raw = present(h.name) or present(h.id) or present(h.slot) or "local"
    return "tern:" .. tostring(raw)
end

local function session_host_id(s: any): string?
    local raw = present(s.host)
    if raw == nil then
        return nil
    end
    return "tern:" .. tostring(raw)
end

local function snapshot(cx: any): any
    local hosts = tern.json.array()
    for _, raw in cx.hosts:list() do
        local h: any = raw
        local state = present(h.state) or present(h.status)
        if state == nil then
            if h.connected == true then
                state = "online"
            elseif h.connected == false then
                state = "offline"
            else
                state = "unknown"
            end
        end

        table.insert(hosts, {
            host_id = host_id(h),
            label = present(h.name) or present(h.label) or tostring(present(h.slot) or "local"),
            mode = if h.local == true then "direct" else "remote",
            status = state,
            rtt_ms = present(h.rtt_ms),
        })
    end

    local sessions = tern.json.array()
    for _, raw in cx.sessions:list() do
        local s: any = raw
        local state = "available"
        if s.current == true then
            state = "current"
        end
        if s.locked == true then
            state = if s.current == true then "current_locked" else "locked"
        end

        table.insert(sessions, {
            session_id = tostring(s.id),
            name = present(s.name),
            host_id = session_host_id(s),
            status = state,
            current = s.current == true,
            locked = s.locked == true,
            tab_count = present(s.tabs),
        })
    end

    return {
        schema = "z0.tern.capacity.v1",
        source = "tern.window",
        hosts = hosts,
        sessions = sessions,
    }
end

local function export(cx: any, toast: boolean?)
    if writing then
        return
    end
    writing = true

    local encoded = tern.json.encode(snapshot(cx), true) .. "\n"
    cx.docs:write(output :: string, encoded):next(function(_result: any, err: any, next_cx: any)
        writing = false
        if err ~= nil then
            tern.log.warn("z0 capacity export failed", err)
            if toast and next_cx ~= nil then
                next_cx:toast("error", "z0 capacity export failed", tostring(err))
            end
            return
        end
        if toast and next_cx ~= nil then
            next_cx:toast("success", "z0 capacity exported", output :: string)
        end
    end)
end

local function tick(cx: any)
    export(cx, false)
    tern.timer(5000, tick)
end

tern.command({
    id = "export-z0-capacity",
    title = "Export z0 capacity snapshot",
    icon = "terminal",
    run = function(cx)
        export(cx, true)
    end,
})

tern.on("window_start", function(cx)
    export(cx, false)
    tern.timer(5000, tick)
end)
