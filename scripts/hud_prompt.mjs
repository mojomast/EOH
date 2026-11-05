// stdin: HUD context JSON; argv[2]: God's Eye View checkout. stdout: {instructions, input}.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const mod = await import(pathToFileURL(path.join(process.argv[2], 'src/hudSummaryResponse.js')).href);
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const context = JSON.parse(raw || '{}');
process.stdout.write(JSON.stringify({
  instructions: mod.hudSummaryInstructions(context?.locale),
  input: mod.hudSummaryInput(context),
}));
