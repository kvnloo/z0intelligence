import type { Call, Json } from "../turn.ts";
import type { Context, EventHandlers, Message, Pi, Services, Tool } from "../native.ts";

/** Implements OMP's branch-before-prompt and cloned per-provider context contract. */
export class FakePi implements Pi {
	hooks: Partial<EventHandlers> = {};
	tools = new Map<string, Tool>();
	commands = new Map<string, Parameters<Pi["registerCommand"]>[1]>();
	activeTools = ["read", "bash"];
	ui: string[] = [];
	history: Message[] = [];
	session = "replay-session";
	toolUpdates = 0;
	ctx: Context = {
		sessionManager: {
			getSessionId: () => this.session,
			getBranch: () => this.history.map(message => ({ type: "message", message })),
		},
		ui: { notify: text => { this.ui.push(text); } },
	};
	on<E extends keyof EventHandlers>(event: E, handler: EventHandlers[E]): void { this.hooks[event] = handler; }
	registerTool(tool: Tool): void { this.tools.set(tool.name, tool); }
	registerCommand(name: string, command: Parameters<Pi["registerCommand"]>[1]): void { this.commands.set(name, command); }
	getActiveTools(): string[] { return this.activeTools; }
	async setActiveTools(names: string[]): Promise<void> { this.toolUpdates++; this.activeTools = names; }
	async start(prompt: Message): Promise<void> {
		await this.hooks.before_agent_start?.({ prompt: String(prompt.content) }, this.ctx);
		this.history.push(prompt);
	}
	async context(): Promise<Message[]> {
		const messages = structuredClone(this.history);
		return (await this.hooks.context?.({ messages }, this.ctx))?.messages ?? messages;
	}
	async command(name: string, args = ""): Promise<void> {
		const command = this.commands.get(name);
		if (!command) throw new Error(`missing command ${name}`);
		await command.handler(args, this.ctx);
	}
	async zoom(): Promise<string> {
		const tool = this.tools.get("optchat_zoom");
		if (!tool) throw new Error("zoom tool missing");
		const row = await tool.execute("zoom", { id: 0, n: 1 });
		return row.content.map(block => block.text).join("\n");
	}
}

/** A deterministic background summarizer; no SDK, model, disk log or credentials. */
export class FakeBackend implements Services {
	on = true;
	ready = true;
	fail = false;
	calls: { op: string; extra: Json }[] = [];
	sources: string[] = [];
	pauses = 0;
	disposals = 0;
	viewRevision = 0;
	enabled = () => this.on;
	setEnabled = (on: boolean) => { this.on = on; };
	pause = () => { this.pauses++; };
	dispose = () => { this.disposals++; };
	call: Call = async (op, extra = {}) => {
		this.calls.push({ op, extra });
		if (this.fail) return { ok: false, error: "synthetic backend failure" };
		if (op === "append") { this.sources.push(String(extra.text)); return { ok: true, i: this.sources.length - 1 }; }
		if (op === "view_ids") {
			if (!Array.isArray(extra.ids)) throw new Error("ids missing");
			return { ok: true, ready: this.ready, view: `<chat>\n${extra.ids.map(id => `${id}+1|summary ${id}, revision ${this.viewRevision}`).join("\n")}\n</chat>` };
		}
		return { ok: true, text: "source detail", path: "synthetic-memory.html" };
	};
}
