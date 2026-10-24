import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
const files = (await readdir('dist', { recursive: true })).sort();
const shell = ['/'];
const hash = createHash('sha256');
for (const file of files) {
  if (file === 'sw.js') continue;
  const path = `dist/${file}`;
  let content;
  try { content = await readFile(path); } catch (error) {
    if (error.code === 'EISDIR') continue;
    throw error;
  }
  hash.update(file).update(content);
  if (file !== 'index.html') shell.push(`/${file}`);
}
const worker = (await readFile('sw.js', 'utf8'))
  .replace('__CACHE_VERSION__', hash.digest('hex').slice(0, 16))
  .replace('__SHELL_ASSETS__', JSON.stringify(shell));
await writeFile('dist/sw.js', worker);
