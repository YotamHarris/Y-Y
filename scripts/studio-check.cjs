const { existsSync } = require('node:fs');
const { join } = require('node:path');
const { spawnSync } = require('node:child_process');
const local = join(__dirname, '..', '.tools', 'studio-venv', 'Scripts', 'python.exe');
const result = spawnSync(existsSync(local) ? local : 'python', [join(__dirname, 'studio-check.py')], { stdio: 'inherit' });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
