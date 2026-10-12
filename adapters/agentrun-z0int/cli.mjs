/** Local operator-only demo. Print a redacted report, never input or provider errors. */
import { readFile } from 'node:fs/promises';
import { runLivePlanReview } from './runner.mjs';

async function main(args = process.argv.slice(2)) {
  const at = args.indexOf('--input');
  if (at < 0 || !args[at + 1] || !args.includes('--allow-remote-jev')) {
    console.error('Usage: node cli.mjs --input ./request.json --allow-remote-jev');
    return 2;
  }
  try {
    const input = JSON.parse(await readFile(args[at + 1], 'utf8'));
    const { report } = await runLivePlanReview(input, { allowRemoteJev: true });
    console.log(JSON.stringify(report, null, 2));
    return report.status === 'escalated' ? 2 : 0;
  } catch (error) {
    if (error?.constructor?.name === 'AgentRunSetupError') console.error(error.message);
    else console.error('AgentRun plan review failed. Check local service, authorized Jev access, and workflow input.');
    return 1;
  }
}
process.exitCode = await main();
