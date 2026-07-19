import { cp, mkdir, readdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url))
const distRoot = path.join(root, 'dist')
const hermesHome = process.env.HERMES_HOME || path.join(homedir(), '.hermes')
const pluginsRoot = path.join(hermesHome, 'desktop-plugins')
const requestedIds = process.argv.slice(2)

async function main() {
  if (requestedIds.length > 1) {
    throw new Error('Usage: npm run deploy -- [plugin-id]')
  }

  const entries = await readdir(distRoot, { withFileTypes: true })
  const availableIds = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
  const ids = requestedIds.length === 1 ? requestedIds : availableIds

  if (requestedIds.length === 1 && !availableIds.includes(requestedIds[0])) {
    throw new Error(`No built plugin found at dist/${requestedIds[0]}`)
  }

  for (const id of ids) {
    const source = path.join(distRoot, id)
    const destination = path.join(pluginsRoot, id)
    await mkdir(destination, { recursive: true })
    await cp(source, destination, { recursive: true, force: true })
    console.log(`Deployed ${id} to ${destination}`)
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
