--!strict
-- Carly navigation fast path, fed by and feeding the z0int carly-nav inlet.
--
-- Two entries share one router:
-- * Carly (typed or voice): a request-context line tells her to call
--   `plugins['carly-nav'].go(<request>)` first. Tern has no hook before her
--   model turn, so this replaces her guessing with one call.
-- * Quick navigation prompt (ctrl+alt+shift+space): no model turn at all.
--
-- Router: local intent (next/previous/Nth tab, name match), then TypeSafe Jev
-- over the learned navigation actions (`z0int carly-nav decide`), else not
-- handled. Every request and outcome is recorded for `z0int carly-nav learn`.

local OVERLAP_FLOOR = 0.45 -- Handsfree local_focus: top label share
local OVERLAP_MARGIN = 0.15 -- and lead over the runner-up
local DECIDE_DEADLINE_MS = 2500 -- python start + catalog + one 0.8 s Jev request
local UNDO_WINDOW_MS = 4000 -- focus returning here this soon means the action was wrong
local MAX_TARGETS = 64
local UNIVERSE_EVERY_MS = 6 * 3600 * 1000

-- Load must stay far under Tern's 50 ms budget, so nothing here touches disk.
-- The inlet's Python source is found on first use: Z0INT_SRC, then the path
-- install.sh records in plugin data (installed copy), then the linked checkout.
local src_path: string? = nil
local function z0int_src(): string
	if src_path == nil then
		local recorded = tern.plugin.data .. "/z0int-src"
		local from_install = tern.fs.exists(recorded) and string.match(tern.fs.read(recorded), "^%s*(.-)%s*$") or nil
		src_path = tern.getenv("Z0INT_SRC") or from_install or (tern.plugin.dir .. "/../../src")
	end
	return src_path :: string
end
local PYTHON = tern.getenv("Z0INT_PYTHON") or "python3"

type Candidate = { id: string, label: string, pane: number, words: { string } }
type Decision = { action: string, exec: { type: string, name: string? }?, pane: number?, label: string?, backend: string, confidence: number? }
type Result = { handled: boolean, did: string?, backend: string?, ms: number?, episode: string? }

local function set(words: string): { [string]: boolean }
	local out = {}
	for word in words:gmatch("%S+") do
		out[word] = true
	end
	return out
end

local STOP = set("a an the to of for this that with on in at and or please my me can could would you u i")
local FILLER = set("switch go move rotate skip jump focus show open bring activate take tab tabs window block pane one")
local FOCUS_VERBS = set("switch go focus show open bring activate jump take")
local NEXT_WORDS = set("next right forward")
local PREV_WORDS = set("prev previous left back")
local ORDINALS: { [string]: number } = {
	first = 1, second = 2, third = 3, fourth = 4, fifth = 5,
	sixth = 6, seventh = 7, eighth = 8, ninth = 9, tenth = 10,
	one = 1, two = 2, three = 3, four = 4, five = 5, six = 6, seven = 7, eight = 8, nine = 9, ten = 10,
}

local pending_undo: { episode: string, pane: number, at: number }? = nil
local last_universe = -math.huge

local function inlet(command: string, payload: { [string]: any }, cb: ((any, any) -> ())?)
	tern.process.run({ PYTHON, "-m", "z0int.carly_nav", command }, {
		env = { PYTHONPATH = z0int_src() },
		stdin = tern.json.encode(payload),
		timeout_ms = command == "decide" and DECIDE_DEADLINE_MS or 10000,
	}, function(result, cx)
		if cb then
			local ok, value = pcall(tern.json.decode, result.stdout)
			cb(result.status == 0 and ok and value or nil, cx)
		end
	end)
end

local function outcome(episode: string, what: string)
	inlet("record", { kind = "outcome", episode = episode, outcome = what })
end

local function new_episode(): string
	return string.format("l%x%04x", os.time(), math.random(0, 0xffff))
end

local function tokenize(text: string): { string }
	local words = {}
	for word in string.lower(text):gsub("[^%w]+", " "):gmatch("%S+") do
		if not STOP[word] then
			table.insert(words, word)
		end
	end
	return words
end

local function basename(path: string?): string
	if path == nil or path == "" then
		return ""
	end
	return string.match(path, "([^/]+)/*$") or ""
end

-- Tabs of the session the focused block is in, by position.
local function session_tabs(cx): { any }
	local focused = cx.session:focused()
	local tab_id
	for _, pane in cx.session:panes() do
		if pane.pane == focused then
			tab_id = pane.tab
		end
	end
	local all = cx.session:tabs()
	local session
	for _, tab in all do
		if tab.id == tab_id then
			session = tab.session
		end
	end
	local out = {}
	for _, tab in all do
		if session == nil or tab.session == session then
			table.insert(out, tab)
		end
	end
	table.sort(out, function(a, b) return a.position < b.position end)
	return out
end

local function candidates(cx): { Candidate }
	local list: { Candidate } = {}
	-- `label` is what Jev reads; `name` alone is what local overlap scores, so a
	-- program or directory in the label cannot dilute an exact name match.
	local function add(id: string, label: string, name: string, pane: number)
		if #list < MAX_TARGETS and label ~= "" then
			table.insert(list, { id = id, label = label, pane = pane, words = tokenize(name) })
		end
	end
	for _, tab in cx.session:tabs() do
		local name = (tab.name ~= nil and tab.name ~= "") and tab.name or tab.title
		add("t" .. tostring(tab.id), string.format("tab %d: %s — %s %s", tab.position, name, tab.program, basename(tab.cwd)), name, tab.pane)
	end
	for _, pane in cx.session:panes() do
		if not pane.parked and pane.kind ~= "carly" then
			local what = (pane.program or pane.kind or "") .. " " .. basename(pane.cwd or pane.path)
			add("p" .. tostring(pane.pane), string.format("%s block: %s — %s", pane.kind or "block", pane.title, what), pane.title, pane.pane)
		end
	end
	return list
end

local function overlap_focus(goal: { string }, list: { Candidate }): Decision?
	if #goal == 0 then
		return nil
	end
	local wanted: { [string]: boolean } = {}
	for _, word in goal do
		wanted[word] = true
	end
	local best: { [number]: { score: number, label: string } } = {}
	for _, candidate in list do
		if #candidate.words > 0 then
			local hits = 0
			for _, word in candidate.words do
				if wanted[word] then
					hits += 1
				end
			end
			local score = hits / #candidate.words
			local seen = best[candidate.pane]
			if seen == nil or score > seen.score then
				best[candidate.pane] = { score = score, label = candidate.label }
			end
		end
	end
	local top, second, top_pane = -1, -1, nil :: number?
	for pane, row in best do
		if row.score > top then
			second, top, top_pane = top, row.score, pane
		elseif row.score > second then
			second = row.score
		end
	end
	if top_pane == nil or top < OVERLAP_FLOOR or (second >= 0 and top - second < OVERLAP_MARGIN) then
		return nil
	end
	return { action = "focus", exec = { type = "focus" }, pane = top_pane, label = best[top_pane].label, backend = "local", confidence = math.min(0.95, top) }
end

local function tab_action(name: string): Decision
	return { action = name, exec = { type = "tern_action", name = name }, backend = "local" }
end

-- "first tab", "tab 3", "last tab": position in the current session.
local function ordinal_tab(cx, words: { string }): Decision?
	local wanted: number? = nil
	for i, word in words do
		local after = words[i + 1]
		if after == "tab" and (ORDINALS[word] or tonumber(word) or word == "last") then
			wanted = word == "last" and math.huge or ORDINALS[word] or tonumber(word)
		elseif word == "tab" and after and (tonumber(after) or ORDINALS[after]) then
			wanted = tonumber(after) or ORDINALS[after]
		end
	end
	if wanted == nil then
		return nil
	end
	local tabs = session_tabs(cx)
	local tab = tabs[wanted == math.huge and #tabs or wanted :: number]
	if tab == nil then
		return nil
	end
	local name = (tab.name ~= nil and tab.name ~= "") and tab.name or tab.title
	return { action = "focus", exec = { type = "focus" }, pane = tab.pane, label = string.format("tab %d: %s", tab.position, name), backend = "local", confidence = 1 }
end

local function local_intent(cx, text: string, list: { Candidate }): Decision?
	local words = tokenize(text)
	local rest: { string } = {}
	local verb = false
	for i, word in words do
		if FOCUS_VERBS[word] then
			verb = true
		end
		if not FILLER[word] then
			table.insert(rest, word)
		end
		if words[i + 1] == "tab" and NEXT_WORDS[word] then
			return tab_action("next_tab")
		elseif words[i + 1] == "tab" and PREV_WORDS[word] then
			return tab_action("previous_tab")
		end
	end
	local ordinal = ordinal_tab(cx, words)
	if ordinal then
		return ordinal
	end
	if #rest == 1 and NEXT_WORDS[rest[1]] then
		return tab_action("next_tab")
	end
	if #rest == 1 and PREV_WORDS[rest[1]] then
		return tab_action("previous_tab")
	end
	if verb and #rest > 0 then
		local decision = overlap_focus(rest, list)
		if decision then
			return decision
		end
	end
	return overlap_focus(words, list)
end

-- Runs a decision; returns what it did, or nil when nothing was executable.
local function apply(cx, decision: Decision, episode: string, started: number): string?
	local exec = decision.exec
	if exec == nil then
		return nil
	end
	local from = cx.session:focused()
	local did
	if exec.type == "focus" and decision.pane ~= nil then
		cx.layout:focus(decision.pane)
		did = "Switched to " .. (decision.label or "block")
	elseif exec.type == "tern_action" and exec.name then
		local name = exec.name
		if not pcall(function() cx:action(name) end) then
			outcome(episode, "error")
			return nil
		end
		did = (name:gsub("_", " "))
	else
		return nil
	end
	outcome(episode, "applied")
	if from ~= nil then
		pending_undo = { episode = episode, pane = from, at = tern.now() }
	end
	cx:toast("success", did, string.format("%s%s · %d ms", decision.backend,
		decision.confidence and string.format(" %.2f", decision.confidence) or "", math.floor(tern.now() - started)))
	return did
end

local function report_universe(cx)
	if tern.now() - last_universe < UNIVERSE_EVERY_MS then
		return
	end
	last_universe = tern.now()
	local actions = tern.json.array()
	for _, action in cx.actions:list() do
		if action.plugin == nil then
			table.insert(actions, { id = action.id, label = action.label })
		end
	end
	inlet("record", { kind = "universe", actions = actions })
end

local function finish(episode: string, did: string?, backend: string, started: number): Result
	if did == nil then
		outcome(episode, "fallback") -- whoever asked (Carly or the user) handles it; learn reads what they did
	end
	return { handled = did ~= nil, did = did, backend = backend, ms = math.floor(tern.now() - started), episode = episode }
end

-- Returns a Result when the local path decided synchronously; otherwise nil,
-- and `done(result, fresh_cx)` runs once Jev answers.
local function route(cx, text: string, done: (Result, any) -> ()): Result?
	local started = tern.now()
	report_universe(cx)
	local list = candidates(cx)
	local decision = local_intent(cx, text, list)
	if decision then
		local episode = new_episode()
		inlet("record", {
			kind = "episode", id = episode, backend = "local", utterance = text,
			decision = { action = decision.action, confidence = decision.confidence },
		})
		return finish(episode, apply(cx, decision, episode, started), "local", started)
	end
	local request = { utterance = text, candidates = tern.json.array() }
	local panes: { [string]: Candidate } = {}
	for _, candidate in list do
		table.insert(request.candidates, { id = candidate.id, label = candidate.label })
		panes[candidate.id] = candidate
	end
	inlet("decide", request, function(answer, next_cx)
		if next_cx == nil then
			return
		end
		local episode = (answer and answer.episode) or new_episode()
		local did = nil
		if answer and answer.action ~= "none" and answer.exec then
			local target = answer.target and panes[answer.target] or nil
			did = apply(next_cx, {
				action = answer.action, exec = answer.exec, backend = "jev", confidence = answer.confidence,
				pane = target and target.pane or nil, label = target and target.label or nil,
			}, episode, started)
		end
		done(finish(episode, did, "jev", started), next_cx)
	end)
	return nil
end

-- Focus jumping straight back after a fast-path action means it was wrong.
tern.on("focus", function(ev, cx)
	local watch = pending_undo
	if watch == nil then
		return
	end
	if tern.now() - watch.at > UNDO_WINDOW_MS then
		pending_undo = nil
	elseif ev.pane == watch.pane then
		pending_undo = nil
		outcome(watch.episode, "undone")
	end
end)

local function trim(text: any): string
	return type(text) == "string" and (string.match(text, "^%s*(.-)%s*$") or "") or ""
end

tern.command({
	id = "ask",
	title = "Carly: Quick navigation",
	icon = "sparkle",
	group = "Carly",
	keys = { "ctrl+alt+shift+space" },
	run = function(cx)
		cx:prompt({ placeholder = "Go to a tab or block, next, back — or ask Carly", icon = "sparkle", action = "go" })
			:next(function(text, err, next_cx)
				local request = trim(text)
				if err or next_cx == nil or request == "" then
					return
				end
				local sync = route(next_cx, request, function(result, fresh_cx)
					if not result.handled then
						fresh_cx:ask_carly(request)
					end
				end)
				if sync and not sync.handled then
					next_cx:ask_carly(request)
				end
			end)
	end,
})

tern.carly.export("go", {
	sig = "(request: string) -> {handled: boolean, did: string?, backend: string?, ms: number?}",
	doc = "Fast Tern navigation: switch to a tab or block by name, next/previous tab, Nth tab, and learned window actions. Pass the user's own words. handled=true means it is already done; otherwise do it yourself.",
}, function(request: any, cx, call)
	local text = trim(request)
	if text == "" then
		return { handled = false }
	end
	local sync = route(cx, text, function(result)
		call:reply(result)
	end)
	if sync then
		return sync
	end
	return call:wait(DECIDE_DEADLINE_MS + 1000)
end)

tern.carly.context(function()
	return "Navigation (switch/focus a tab or block, next/previous/Nth tab): first run lua "
		.. "`return await(plugins['carly-nav'].go(\"<user's words>\"))`. "
		.. "handled=true means it is done: confirm in a few words. Otherwise do it yourself."
end)
