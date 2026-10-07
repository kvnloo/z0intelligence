import { expect, test } from "bun:test";
import {
	execFileSync,
	spawn,
	spawnSync,
	type ChildProcessWithoutNullStreams,
	type SpawnSyncReturns,
} from "node:child_process";
import { createInterface, type Interface } from "node:readline";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import type { Model } from "@oh-my-pi/pi-ai";
import { getBundledModel } from "@oh-my-pi/pi-catalog/models";
import {
	createAgentSession,
	SessionManager,
	Settings,
	type ExtensionFactory,
} from "@oh-my-pi/pi-coding-agent";
import { ModelRegistry } from "@oh-my-pi/pi-coding-agent/config/model-registry";
import { AuthStorage } from "@oh-my-pi/pi-coding-agent/session/auth-storage";
import { registerReuseAdapter, sha256Utf8, type ReuseConfig } from "./reuse.ts";

const Z0_ROOT = join(import.meta.dir, "../..");
const PYTHON = join(Z0_ROOT, ".venv/bin/python");
const WRITE_CONTENT = "from upstream.records import normalize_rows\nrows = normalize_rows([1, 1, 2])\n";
const verifierArgv = [PYTHON, "-m", "pytest", "-q", "tests/test_records.py"] as const;
const verifierBinding: JsonObject = {
	verifier_id: "pytest:records-owning-test",
	candidate_id: "file:upstream/records.py",
	argv: [...verifierArgv],
	test_paths: ["tests/test_records.py"],
};

type JsonObject = Record<string, unknown>;
type BridgeExchange = { request: JsonObject; response: unknown };
type PendingReply = {
	resolve: (value: unknown) => void;
	reject: (error: Error) => void;
	timer: ReturnType<typeof setTimeout>;
};

function isObject(value: unknown): value is JsonObject {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function messageContainsReuseContext(message: unknown): boolean {
	if (!isObject(message)) return false;
	if (typeof message.content === "string") return message.content.includes("<z0int-reuse-context>");
	if (!Array.isArray(message.content)) return false;
	return message.content.some(part =>
		isObject(part) && part.type === "text" && typeof part.text === "string" &&
		part.text.includes("<z0int-reuse-context>"),
	);
}

function stripReuseContextFromPayload(payload: unknown): unknown {
	if (!isObject(payload) || !Array.isArray(payload.messages)) return payload;
	return { ...payload, messages: payload.messages.filter(message => !messageContainsReuseContext(message)) };
}

function jsonLine(value: JsonObject): string {
	return `${JSON.stringify(value)}\n`;
}

function runOwningVerifier(repoRoot: string): SpawnSyncReturns<string> {
	return spawnSync(verifierArgv[0], verifierArgv.slice(1), {
		cwd: repoRoot,
		encoding: "utf8",
		env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
		timeout: 30_000,
	});
}

function sseResponse(modelId: string, toolCall: boolean, target: string): Response {
	const chunks = toolCall
		? [
				{
					id: "reuse-real-bridge",
					object: "chat.completion.chunk",
					created: 1,
					model: modelId,
					choices: [{
						index: 0,
						delta: {
							tool_calls: [{
								index: 0,
								id: "call-write",
								type: "function",
								function: {
									name: "write",
									arguments: JSON.stringify({ path: target, content: WRITE_CONTENT }),
								},
							}],
						},
						finish_reason: null,
					}],
				},
				{
					id: "reuse-real-bridge",
					object: "chat.completion.chunk",
					created: 1,
					model: modelId,
					choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
				},
			]
		: [
				{
					id: "reuse-real-bridge",
					object: "chat.completion.chunk",
					created: 1,
					model: modelId,
					choices: [{ index: 0, delta: { content: "The existing implementation was used." }, finish_reason: null }],
				},
				{
					id: "reuse-real-bridge",
					object: "chat.completion.chunk",
					created: 1,
					model: modelId,
					choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
				},
			];
	const body = `${chunks.map(chunk => `data: ${JSON.stringify(chunk)}`).join("\n\n")}\n\ndata: [DONE]\n\n`;
	return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

class JsonlBridgeWorker {
	readonly #child: ChildProcessWithoutNullStreams;
	readonly #lines: Interface;
	readonly #pending = new Map<string, PendingReply>();
	readonly #exited: Promise<void>;
	#nextId = 0;

	private constructor(child: ChildProcessWithoutNullStreams) {
		this.#child = child;
		this.#lines = createInterface({ input: child.stdout });
		this.#lines.on("line", line => this.#receive(line));
		child.stderr.resume();
		this.#exited = new Promise(resolve => child.once("exit", () => resolve()));
		child.once("error", error => this.#rejectPending(error));
	}

	static async start(stateRoot: string, repoRoot: string, wrapperPath: string): Promise<JsonlBridgeWorker> {
		const srcRoot = join(Z0_ROOT, "src");
		const child = spawn(PYTHON, ["-u", wrapperPath], {
			cwd: Z0_ROOT,
			stdio: "pipe",
			env: {
				...process.env,
				PYTHONPATH: [srcRoot, process.env.PYTHONPATH].filter(Boolean).join(":"),
				Z0INT_HOME: stateRoot,
				Z0INT_ROOT: Z0_ROOT,
				Z0INT_FIXTURE_REPO: repoRoot,
			},
		});
		const worker = new JsonlBridgeWorker(child);
		const hello = await worker.request({ op: "hello" }, 10_000);
		if (!isObject(hello) || hello.ok !== true || hello.protocol !== "z0int.bridge.v2") {
			await worker.close();
			throw new Error("existing JSONL bridge worker failed its protocol handshake");
		}
		return worker;
	}

	request = (body: JsonObject, timeoutMs: number): Promise<unknown> => {
		const id = String(++this.#nextId);
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				this.#pending.delete(id);
				reject(new Error(`JSONL bridge request ${id} timed out`));
			}, timeoutMs);
			this.#pending.set(id, { resolve, reject, timer });
			try {
				this.#child.stdin.write(jsonLine({ id, ...body }));
			} catch (error) {
				clearTimeout(timer);
				this.#pending.delete(id);
				reject(error instanceof Error ? error : new Error(String(error)));
			}
		});
	};

	async close(): Promise<void> {
		if (this.#child.exitCode !== null || this.#child.signalCode !== null) return;
		try {
			await this.request({ op: "shutdown" }, 2_000);
		} finally {
			this.#child.stdin.end();
			await Promise.race([
				this.#exited,
				new Promise<void>(resolve => setTimeout(resolve, 2_000)),
			]);
			if (this.#child.exitCode === null && this.#child.signalCode === null) this.#child.kill();
			this.#lines.close();
		}
	}

	#receive(line: string): void {
		let parsed: unknown;
		try {
			parsed = JSON.parse(line);
		} catch {
			this.#rejectPending(new Error("JSONL bridge returned invalid JSON"));
			return;
		}
		if (!isObject(parsed) || typeof parsed.id !== "string") {
			this.#rejectPending(new Error("JSONL bridge response had no request id"));
			return;
		}
		const pending = this.#pending.get(parsed.id);
		if (!pending) return;
		clearTimeout(pending.timer);
		this.#pending.delete(parsed.id);
		pending.resolve(parsed);
	}

	#rejectPending(error: Error): void {
		for (const pending of this.#pending.values()) {
			clearTimeout(pending.timer);
			pending.reject(error);
		}
		this.#pending.clear();
	}
}

function fixtureSearchWorkerSource(useFixtureSearch: boolean): string {
	const lines = useFixtureSearch
		? [
				"import z0int.context_resolve as context_resolve",
				"def fixture_search(root, query_text, *, kind, limit):",
				"    return {",
				"        'status': 'ready', 'coverage': 'complete',",
				"        'generation': 'fixture:epoch=1', 'generation_status': 'tracked',",
				"        'generation_reliable': True, 'watcher_ready': True, 'warmup_complete': True,",
				"        'package_version': 'test-fixture', 'index_epoch': 1,",
				"        'path_hits': [],",
				"        'content_hits': [",
				"            {'path': 'upstream/records.py', 'line': 1, 'text': 'def normalize_rows(rows):'},",
				"            {'path': 'tests/test_records.py', 'line': 3, 'text': 'def test_normalize_rows():'},",
				"        ],",
				"    }",
				"context_resolve._fff_search_repository = fixture_search",
			]
		: [];
	lines.push(
		"import z0int.bridge.runtime as bridge_runtime",
		"bridge_runtime.preflight = lambda prompt: {'route': 'local', 'capability_id': 'coding.reuse', 'label': 'REUSE', 'p': 0.9}",
		"from z0int.bridge.worker import main",
		"raise SystemExit(main())",
	);
	return lines.join("\n");
}

function initFixtureRepository(root: string): void {
	execFileSync("git", ["init", "-q", root]);
	execFileSync("git", ["-C", root, "config", "user.name", "Bridge Fixture"]);
	execFileSync("git", ["-C", root, "config", "user.email", "bridge-fixture@example.invalid"]);
	execFileSync("git", ["-C", root, "remote", "add", "origin", "https://github.com/example/known-library.git"]);
	execFileSync("git", ["-C", root, "add", "upstream", "tests", "zer0.repo.yaml"]);
	execFileSync("git", ["-C", root, "commit", "-qm", "repository evidence fixture"]);
}

function openAiModel(): Model<"openai-completions"> {
	const bundled = getBundledModel<"openai-completions">("openai", "gpt-4o-mini");
	if (!bundled) throw new Error("Expected bundled OpenAI Completions fixture model");
	return { ...bundled, api: "openai-completions" };
}

const runtimeScenarios = [
	{ name: "fixture-backed witness", stripContext: false, useFixtureSearch: true },
	{ name: "context stripping blocks write", stripContext: true, useFixtureSearch: true },
	{ name: "resident FFF discovery reaches native write", stripContext: false, useFixtureSearch: false },
] as const;

for (const scenario of runtimeScenarios) {
	const { stripContext, useFixtureSearch } = scenario;
	test(`patched final provider payload ${scenario.name}`, async () => {
		const tempRoot = await mkdtemp(join(tmpdir(), "z0int-reuse-real-bridge-"));
		const repoRoot = join(tempRoot, "repository");
		const registryPath = join(tempRoot, "components.yaml");
		const target = join(repoRoot, "result.py");
		const model = openAiModel();
		const transportBodies: string[] = [];
		const finalizedBodies: string[] = [];
		const beforePayloads: unknown[] = [];
		const exchanges: BridgeExchange[] = [];
		let providerCalls = 0;
		let activeTraceId: string | null = null;
		let sessionId: string | null = null;
		let worker: JsonlBridgeWorker | null = null;
		let session: Awaited<ReturnType<typeof createAgentSession>>["session"] | null = null;
		let manager: SessionManager | null = null;
		let authStorage: AuthStorage | null = null;
		let verifierRan = false;
		let verifierStatus: number | null = null;
		let verificationSource: string | null = null;
		let turnCloseResponse: unknown = null;
		const previousFetch = globalThis.fetch;
		try {
			await mkdir(join(repoRoot, "upstream"), { recursive: true });
			await mkdir(join(repoRoot, "tests"), { recursive: true });
			await writeFile(join(repoRoot, "upstream/records.py"),
				"def normalize_rows(rows):\n    return list(dict.fromkeys(rows))\n", "utf8");
			await writeFile(join(repoRoot, "tests/test_records.py"),
				"import runpy\nfrom upstream.records import normalize_rows\n"
				+ "def test_normalize_rows():\n    assert normalize_rows([1, 1, 2]) == [1, 2]\n"
				+ "    result = runpy.run_path('result.py')\n    assert result['rows'] == [1, 2]\n", "utf8");
			await writeFile(join(repoRoot, "zer0.repo.yaml"),
				"version: 1\nrepo: example/known-library\narchitecture:\n  subsystems:\n"
				+ "  - id: records\n    paths: [upstream/records.py, tests/test_records.py]\n", "utf8");
			await writeFile(registryPath,
				"components:\n  records:\n    repo: example/known-library\n    owner: Library\n"
				+ "    boundaries:\n      owns: [row normalization]\n", "utf8");
			await writeFile(target, "raise RuntimeError('native write has not produced the result')\n", "utf8");
			initFixtureRepository(repoRoot);
			const baselineVerifier = runOwningVerifier(repoRoot);
			expect(baselineVerifier.status).not.toBe(0);

			const wrapperPath = join(tempRoot, "worker-fixture.py");
			await writeFile(wrapperPath, fixtureSearchWorkerSource(useFixtureSearch), "utf8");
			worker = await JsonlBridgeWorker.start(join(tempRoot, "z0int-home"), repoRoot, wrapperPath);

			const providerFetch: typeof fetch = Object.assign(
				async (_input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
					if (typeof init?.body !== "string") throw new Error("provider request body was not the observed JSON string");
					transportBodies.push(init.body);
					const firstRequest = providerCalls++ === 0;
					return sseResponse(model.id, firstRequest, target);
				},
				{ preconnect: globalThis.fetch.preconnect },
			);
			globalThis.fetch = providerFetch;
			const agentDir = join(tempRoot, "agent");
			await mkdir(agentDir, { recursive: true });
			authStorage = await AuthStorage.create(join(tempRoot, "auth.db"));
			authStorage.keys.setRuntime("openai", "mock-openai-key");
			const registry = new ModelRegistry(authStorage, join(tempRoot, "models.yml"));
			const settings = await Settings.loadIsolated({ cwd: repoRoot, agentDir, inMemory: true });
			manager = SessionManager.inMemory(repoRoot);
			await manager.setSessionName("real reuse bridge boundary", "user");
			const reuseConfig: ReuseConfig = {
				mode: "enforce",
				canonicalRepo: "records",
				registryPath,
				candidateRoots: [repoRoot],
				taskId: "real-bridge-provider-boundary-test",
				verifierBinding,
			};
			const verifyAndClose = async (): Promise<void> => {
				if (verifierRan) return;
				if (!worker || !activeTraceId || !sessionId) throw new Error("trace identity was lost before outcome join");
				const verifierResult = runOwningVerifier(repoRoot);
				verifierRan = true;
				verifierStatus = verifierResult.status;
				const verifierEvidence = join(tempRoot, "owning-test-result.json");
				await writeFile(verifierEvidence, JSON.stringify({
					verifier_id: verifierBinding.verifier_id,
					candidate_id: verifierBinding.candidate_id,
					argv: verifierArgv,
					test_paths: ["tests/test_records.py"],
					returncode: verifierResult.status,
					stdout: verifierResult.stdout,
					stderr: verifierResult.stderr,
					error: verifierResult.error?.message ?? null,
				}, null, 2), "utf8");
				verificationSource = pathToFileURL(verifierEvidence).href;
				const turnCloseRequest: JsonObject = {
					op: "turn_close",
					trace_id: activeTraceId,
					session_id: sessionId,
					omp_pid: process.pid,
					payload: {
						trace_id: activeTraceId,
						session_id: sessionId,
						execution_completed: true,
						verified_success: verifierResult.status === 0,
						verification_source: verificationSource,
						source: "independent_verifier",
					},
				};
				turnCloseResponse = await worker.request(turnCloseRequest, 10_000);
				exchanges.push({ request: turnCloseRequest, response: turnCloseResponse });
			};
			const extension: ExtensionFactory = pi => {
				pi.on("before_provider_request", event => {
					beforePayloads.push(event.payload);
					if (stripContext) return stripReuseContextFromPayload(event.payload);
				});
				pi.on("provider_payload_finalized", event => {
					finalizedBodies.push(event.serializedBody);
				});
				pi.on("tool_result", async event => {
					if (event.toolName === "write") await verifyAndClose();
				});
				pi.on("before_agent_start", async (event, context) => {
					sessionId = context.sessionManager.getSessionId();
					activeTraceId = `trace-${sessionId}`;
					if (!worker) throw new Error("real JSONL worker is unavailable before turn open");
					const opened = await worker.request({
						op: "turn_open",
						trace_id: activeTraceId,
						session_id: sessionId,
						omp_pid: process.pid,
						payload: {
							prompt: event.prompt,
							session_id: sessionId,
							omp_pid: process.pid,
						},
					}, 10_000);
					exchanges.push({
						request: { op: "turn_open", trace_id: activeTraceId, session_id: sessionId },
						response: opened,
					});
					if (!isObject(opened) || opened.ok !== true) throw new Error("existing JSONL worker failed to open the scripted trace");
				});
				registerReuseAdapter(pi, {
					config: reuseConfig,
					getTraceId: () => activeTraceId,
					request: async (body, timeoutMs) => {
						if (!worker) throw new Error("real JSONL worker is unavailable");
						const response = await worker.request(body, timeoutMs);
						exchanges.push({ request: body, response });
						return response;
					},
				});
			};
			const created = await createAgentSession({
				cwd: repoRoot,
				agentDir,
				settings,
				sessionManager: manager,
				authStorage,
				model,
				modelRegistry: registry,
				autoApprove: true,
				hasUI: true,
				cacheWarming: false,
				disableExtensionDiscovery: true,
				enableMCP: false,
				enableLsp: false,
				skipPythonPreflight: true,
				skills: [],
				rules: [],
				contextFiles: [],
				extensions: [extension],
			});
			session = created.session;

			await session.prompt("normalize_rows");
			if (!verifierRan) await verifyAndClose();

			const resolve = exchanges.find(exchange => exchange.request.op === "reuse_resolve");
			const injection = exchanges.find(exchange => exchange.request.op === "reuse_injected");
			const modelInput = exchanges.find(exchange => exchange.request.op === "reuse_model_input");
			const check = exchanges.find(exchange => exchange.request.op === "reuse_check");
			const turnOpen = exchanges.find(exchange => exchange.request.op === "turn_open");
			const injectionIndex = exchanges.findIndex(exchange => exchange.request.op === "reuse_injected");
			const modelInputIndex = exchanges.findIndex(exchange => exchange.request.op === "reuse_model_input");
			const checkIndex = exchanges.findIndex(exchange => exchange.request.op === "reuse_check");
			const resolveRequest = resolve?.request ?? {};
			const resolveResult = isObject(resolve?.response) ? resolve.response : {};
			const injectionResult = isObject(injection?.response) ? injection.response : {};
			const injectionPayload = isObject(injection?.request.payload) ? injection.request.payload : {};
			const modelInputRequest = modelInput?.request ?? {};
			const modelInputResult = isObject(modelInput?.response) ? modelInput.response : {};
			const modelInputPayload = isObject(modelInputRequest.payload) ? modelInputRequest.payload : {};
			const checkResult = isObject(check?.response) ? check.response : {};
			const checkDecision = isObject(checkResult.decision) ? checkResult.decision : {};
			const checkRequest = check?.request ?? {};
			const checkPayload = isObject(checkRequest.payload) ? checkRequest.payload : {};
			const checkVerifierBinding = isObject(checkResult.verifier_binding) ? checkResult.verifier_binding : {};
			const serializedProviderBody = transportBodies[0];
			const parsedProviderBody: unknown = serializedProviderBody ? JSON.parse(serializedProviderBody) : null;
			const providerMessages = isObject(parsedProviderBody) && Array.isArray(parsedProviderBody.messages)
				? parsedProviderBody.messages
				: [];
			const providerBodyText = providerMessages.map(message => {
				if (!isObject(message)) return "";
				if (typeof message.content === "string") return message.content;
				if (!Array.isArray(message.content)) return "";
				return message.content
					.filter((part): part is JsonObject => isObject(part) && part.type === "text" && typeof part.text === "string")
					.map(part => String(part.text)).join("\n");
			}).join("\n");
			const contextStart = providerBodyText.indexOf("Untrusted repository context follows.");
			const contextEnd = providerBodyText.indexOf("</z0int-reuse-context>", contextStart);
			const witnessedContextBlock = contextStart >= 0 && contextEnd >= contextStart
				? providerBodyText.slice(contextStart, contextEnd + "</z0int-reuse-context>".length)
				: "";

			const resolvePayload = isObject(resolveRequest.payload) ? resolveRequest.payload : {};
			const resolveVerifierBinding = isObject(resolvePayload.verifier_binding) ? resolvePayload.verifier_binding : {};
			const resolvedPacket = isObject(resolveResult.packet) ? resolveResult.packet : {};
			const turnOpenRequest = turnOpen?.request ?? {};
			const turnOpenResponse = isObject(turnOpen?.response) ? turnOpen.response : {};
			const injectionRequest = injection?.request ?? {};
			if (!modelInput) {
				const exchangeSummary = exchanges.map(exchange => ({
					op: exchange.request.op,
					ok: isObject(exchange.response) ? exchange.response.ok : null,
					error: isObject(exchange.response) ? exchange.response.error : null,
					mode: isObject(exchange.response) && isObject(exchange.response.decision)
						? exchange.response.decision.mode
						: null,
				}));
				throw new Error(`final observer did not witness provider requests; exchanges=${JSON.stringify(exchangeSummary)}, providerCalls=${providerCalls}, beforePayloads=${beforePayloads.length}, finalizedBodies=${finalizedBodies.length}`);
			}
			expect(resolveResult.ok).toBe(true);
			if (!useFixtureSearch) {
				const packetContext = isObject(resolvedPacket.context) ? resolvedPacket.context : {};
				const recipe = isObject(packetContext.recipe) ? packetContext.recipe : {};
				const operations = Array.isArray(recipe.operations) ? recipe.operations : [];
				const searchOperation = operations.find(operation => isObject(operation) && operation.op === "fff_search");
				const searchDetails = isObject(searchOperation) ? searchOperation : {};
				const candidates = Array.isArray(resolvedPacket.reuse_candidates) ? resolvedPacket.reuse_candidates : [];
				expect(searchDetails.status).toBe("ready");
				expect(searchDetails.coverage).toBe("complete");
				expect(searchDetails.generation).toMatch(/^0\.11\.0:resident=/);
				expect(searchDetails.generation_status).toBe("tracked");
				expect(searchDetails.generation_reliable).toBe(true);
				expect(searchDetails.watcher_ready).toBe(true);
				expect(searchDetails.warmup_complete).toBe(true);
				expect(searchDetails.scanning).toBe(false);
				expect(candidates.some(candidate => {
					if (!isObject(candidate) || !Array.isArray(candidate.evidence) || !Array.isArray(candidate.related_tests)) return false;
					const ownsImplementation = candidate.evidence.some(ref => isObject(ref) && String(ref.locator).endsWith("upstream/records.py"));
					const hasOwningTest = candidate.related_tests.some(ref => isObject(ref) && String(ref.locator).endsWith("tests/test_records.py"));
					return ownsImplementation && hasOwningTest;
			})).toBe(true);
				const resolveDecision = isObject(resolveResult.decision) ? resolveResult.decision : {};
				expect(resolveDecision.mode).toBe("REUSE");
			}
			expect(resolveRequest.trace_id).toBe(activeTraceId);
			expect(turnOpen).toBeDefined();
			expect(turnOpenRequest.trace_id).toBe(activeTraceId);
			expect(turnOpenRequest.session_id).toBe(sessionId);
			expect(turnOpenResponse.ok).toBe(true);
			expect(exchanges.findIndex(exchange => exchange.request.op === "turn_open")).toBeLessThan(
				exchanges.findIndex(exchange => exchange.request.op === "reuse_resolve"),
			);
			expect(resolvePayload.trace_id).toBe(activeTraceId);
			expect(resolvePayload.session_id).toBe(sessionId);
			expect(resolveVerifierBinding.verifier_id).toBe("pytest:records-owning-test");
			expect(resolveVerifierBinding.candidate_id).toBe("file:upstream/records.py");
			expect(resolveVerifierBinding.test_paths).toEqual(["tests/test_records.py"]);
			expect(injectionResult.ok).toBe(true);
			expect(modelInputResult.ok).toBe(!stripContext);
			expect(injectionRequest.trace_id).toBe(activeTraceId);
			expect(injectionRequest.session_id).toBe(sessionId);
			expect(injectionPayload.trace_id).toBe(activeTraceId);
			expect(injectionPayload.session_id).toBe(sessionId);
			expect(modelInputRequest.trace_id).toBe(activeTraceId);
			expect(modelInputRequest.session_id).toBe(sessionId);
			expect(modelInputRequest.trace_id).toBe(resolveRequest.trace_id);
			expect(modelInputPayload.trace_id).toBe(activeTraceId);
			expect(modelInputPayload.session_id).toBe(sessionId);
			expect(modelInputPayload.packet_id).toBe(resolveResult.packet_id);
			expect(injectionPayload.packet_id).toBe(resolveResult.packet_id);
			expect(modelInputPayload.injection_id).toBe(injectionResult.event_id);
			expect(modelInputPayload.boundary).toBe("sdk_final_payload");
			expect(modelInputPayload.request_sha256).toBe(sha256Utf8(serializedProviderBody ?? ""));
			expect(checkResult.ok).toBe(!stripContext);
			expect(checkRequest.trace_id).toBe(resolveRequest.trace_id);
			expect(checkRequest.session_id).toBe(resolvePayload.session_id);
			expect(checkPayload.trace_id).toBe(activeTraceId);
			expect(checkPayload.session_id).toBe(sessionId);
			expect(checkPayload.packet_id).toBe(resolveResult.packet_id);
			expect(checkPayload.tool_name).toBe("write");
			expect(checkPayload.target_paths).toEqual([target]);
			if (!stripContext) {
				expect(checkVerifierBinding.verifier_id).toBe("pytest:records-owning-test");
				expect(checkVerifierBinding.candidate_id).toBe("file:upstream/records.py");
				expect(checkVerifierBinding.test_paths).toEqual(["tests/test_records.py"]);
				const boundTests = Array.isArray(checkVerifierBinding.tests) ? checkVerifierBinding.tests : [];
				expect(boundTests.some(item => isObject(item) && item.path === "tests/test_records.py" &&
					typeof item.source_id === "string" && item.source_id.endsWith("/tests/test_records.py") &&
					typeof item.source_version === "string")).toBe(true);
			}
			expect(injectionIndex).toBeLessThan(modelInputIndex);
			expect(modelInputIndex).toBeLessThan(checkIndex);
			expect(providerCalls).toBe(2);
			if (stripContext) {
				expect(modelInputPayload.context_sha256).toBe("missing");
				expect(modelInputPayload.context_sha256).not.toBe(injectionPayload.context_sha256);
				expect(providerBodyText).not.toContain("<z0int-reuse-context>");
				expect(checkResult.valid).toBe(false);
				expect(checkResult.status).toBe("unavailable");
				expect(checkDecision.mode).toBe("OBSERVE");
				expect(await readFile(target, "utf8")).toContain("native write has not produced");
			} else {
				expect(injectionPayload.context_sha256).toBe(modelInputPayload.context_sha256);
				expect(modelInputPayload.context_sha256).toBe(sha256Utf8(witnessedContextBlock));
				expect(providerBodyText).toContain("<z0int-reuse-context>");
				expect(providerBodyText).toContain("normalize_rows");
				expect(checkResult.valid).toBe(true);
				expect(checkResult.status).toBe("current");
				expect(checkDecision.mode).toBe("REUSE");
				expect(await readFile(target, "utf8")).toBe(WRITE_CONTENT);
			}

			if (!verifierRan || turnCloseResponse === null || verificationSource === null) {
				throw new Error("owning verifier did not join the native tool result");
			}
			expect(verifierStatus === 0).toBe(!stripContext);
			const turnClose = isObject(turnCloseResponse) ? turnCloseResponse : {};
			const closed = isObject(turnClose.closed) ? turnClose.closed : {};
			const outcomeJoin = isObject(closed.outcome_join) ? closed.outcome_join : {};
			const outcome = isObject(outcomeJoin.outcome) ? outcomeJoin.outcome : {};
			const joinedReceipt = isObject(outcomeJoin.receipt) ? outcomeJoin.receipt : {};
			const receiptExtra = isObject(joinedReceipt.extra) ? joinedReceipt.extra : {};
			const joinedReuse = isObject(receiptExtra.reuse) ? receiptExtra.reuse : {};
			expect(turnClose.ok).toBe(true);
			expect(turnClose.trace_id).toBe(activeTraceId);
			expect(outcomeJoin.trace_id).toBe(activeTraceId);
			expect(outcome.verified_success).toBe(!stripContext);
			expect(outcome.verification_source).toBe(verificationSource);
			expect(outcomeJoin.outcome_tier).toBe(stripContext ? "negative" : "gold");
			expect(joinedReuse.preparation_event_id).toBe(resolveResult.event_id);
			expect(joinedReuse.model_input_event_id).toBe(stripContext ? null : modelInputResult.event_id);
			expect(joinedReuse.mutation_event_id).toBe(stripContext ? null : checkResult.mutation_event_id);
			expect(isObject(joinedReuse.verifier_binding)).toBe(true);
			expect(joinedReuse.packet_id).toBe(resolveResult.packet_id);
			if (!stripContext && isObject(joinedReuse.verifier_binding)) {
				expect(joinedReuse.verifier_binding.verifier_id).toBe("pytest:records-owning-test");
				expect(joinedReuse.verifier_binding.candidate_id).toBe("file:upstream/records.py");
				expect(joinedReuse.verifier_binding.test_paths).toEqual(["tests/test_records.py"]);
			}
			if (!stripContext) {
				const turnCloseIndex = exchanges.findIndex(exchange => exchange.request.op === "turn_close");
				const laterModelInputIndex = exchanges.findIndex((exchange, index) =>
					index > modelInputIndex && exchange.request.op === "reuse_model_input",
				);
				if (laterModelInputIndex >= 0) expect(turnCloseIndex).toBeLessThan(laterModelInputIndex);
			}
			expect(exchanges.findIndex(exchange => exchange.request.op === "reuse_check")).toBeLessThan(
				exchanges.findIndex(exchange => exchange.request.op === "turn_close"),
			);
		} finally {
			globalThis.fetch = previousFetch;
			if (session) await session.dispose();
			if (manager) await manager.close();
			if (worker) await worker.close();
			if (authStorage) authStorage.close();
			await rm(tempRoot, { recursive: true, force: true });
		}
	}, 30_000);
}
