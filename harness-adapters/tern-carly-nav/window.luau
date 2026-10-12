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

-- Tabs of the session this window shows, by position. Not derived from the
-- focused block: while Carly's overlay is open it can hold focus, and remote
-- hosts' sessions also have a "tab 1".
local function session_tabs(cx): ({ any }, number?)
	local session = cx.sessions:current().id
	local out = {}
	for _, tab in cx.session:tabs() do
		if tab.session == session then
			table.insert(out, tab)
		end
	end
	table.sort(out, function(a, b) return a.position < b.position end)
	return out, session
end

-- The tab id holding `pane`, or nil.
local function tab_of(cx, pane: number?): number?
	if pane == nil then
		return nil
	end
	for _, info in cx.session:panes() do
		if info.pane == pane then
			return info.tab
		end
	end
	return nil
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
	-- Only the shown session: goto_tab cannot reach another session's tabs.
	local tabs = session_tabs(cx)
	local here: { [number]: boolean } = {}
	for _, tab in tabs do
		here[tab.id] = true
		local name = (tab.name ~= nil and tab.name ~= "") and tab.name or tab.title
		add("t" .. tostring(tab.id), string.format("tab %d: %s — %s %s", tab.position, name, tab.program, basename(tab.cwd)), name, tab.pane)
	end
	for _, pane in cx.session:panes() do
		if not pane.parked and pane.kind ~= "carly" and pane.tab and here[pane.tab] then
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

local VERIFY_MS = 150 -- actions may apply after the handler returns; look again then
local TAB_MOVES = { next_tab = true, previous_tab = true }

-- Runs a decision, then checks the window really changed before claiming it.
-- `done(did, fresh_cx)`: `did` is nil when nothing ran or the check failed.
-- Live 2026-10-12: `cx.layout:focus(pane)` reported success yet left another
-- tab shown, so tab changes go through Tern's `goto_tab:N` action.
local function apply(cx, decision: Decision, episode: string, started: number, done: (string?, any) -> ())
	local exec = decision.exec
	if exec == nil then
		done(nil, cx)
		return
	end
	local from = cx.session:focused()
	local from_tab = tab_of(cx, from)
	local want_tab: number? = nil
	local did: string
	if exec.type == "focus" and decision.pane ~= nil then
		local pane = decision.pane :: number
		want_tab = tab_of(cx, pane)
		local position
		for _, tab in session_tabs(cx) do
			if tab.id == want_tab then
				position = tab.position
			end
		end
		if position == nil then
			done(nil, cx)
			return
		end
		if not pcall(function()
			cx:action("goto_tab:" .. tostring(position))
			cx.layout:focus(pane)
		end) then
			outcome(episode, "error")
			done(nil, cx)
			return
		end
		did = "Switched to " .. (decision.label or "block")
	elseif exec.type == "tern_action" and exec.name then
		local name = exec.name :: string
		if not pcall(function() cx:action(name) end) then
			outcome(episode, "error")
			done(nil, cx)
			return
		end
		did = (name:gsub("_", " "))
	else
		done(nil, cx)
		return
	end
	tern.timer(VERIFY_MS, function(fresh)
		-- A focused block outside every tab (Carly's overlay) proves nothing either
		-- way; only a block seen in the wrong tab counts as failure.
		local now_tab = tab_of(fresh, fresh.session:focused())
		local failed = false
		if now_tab ~= nil and want_tab ~= nil then
			failed = now_tab ~= want_tab
		elseif now_tab ~= nil and from_tab ~= nil and TAB_MOVES[exec.name or ""] then
			failed = now_tab == from_tab
		end
		if failed then
			outcome(episode, "error")
			done(nil, fresh)
			return
		end
		outcome(episode, "applied")
		if from ~= nil then
			pending_undo = { episode = episode, pane = from, at = tern.now() }
		end
		fresh:toast("success", did, string.format("%s%s · %d ms", decision.backend,
			decision.confidence and string.format(" %.2f", decision.confidence) or "", math.floor(tern.now() - started)))
		done(did, fresh)
	end)
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

-- `done(result, fresh_cx)` runs once, after the action was checked or Jev passed.
local function route(cx, text: string, done: (Result, any) -> ())
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
		apply(cx, decision, episode, started, function(did, fresh)
			done(finish(episode, did, "local", started), fresh)
		end)
		return
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
		if not (answer and answer.action ~= "none" and answer.exec) then
			done(finish(episode, nil, "jev", started), next_cx)
			return
		end
		local target = answer.target and panes[answer.target] or nil
		apply(next_cx, {
			action = answer.action, exec = answer.exec, backend = "jev", confidence = answer.confidence,
			pane = target and target.pane or nil, label = target and target.label or nil,
		}, episode, started, function(did, fresh)
			done(finish(episode, did, "jev", started), fresh)
		end)
	end)
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
				route(next_cx, request, function(result, fresh_cx)
					if not result.handled then
						fresh_cx:ask_carly(request)
					end
				end)
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
	route(cx, text, function(result)
		call:reply(result)
	end)
	return call:wait(DECIDE_DEADLINE_MS + 1000)
end)

-- Tern disables a context provider for the window's lifetime after one call over
-- 4 ms (seen live once, likely a process stall), so the provider does no work.
local NAV_HINT = "Navigation (switch/focus a tab or block, next/previous/Nth tab): first run lua "
	.. "`return await(plugins['carly-nav'].go(\"<user's words>\"))`. "
	.. "handled=true means it is done: confirm in a few words. Otherwise do it yourself."
tern.carly.context(function()
	return NAV_HINT
end)
