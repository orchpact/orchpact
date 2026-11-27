// Build only public browser assets. The server and the published site need no Node runtime.
import { execFileSync } from "node:child_process"
import { mkdtemp, readFile, writeFile, readdir, mkdir, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"

const root = path.dirname(fileURLToPath(import.meta.url))
const outdir = path.resolve(root, "../src/orchpact/static/ui")
const entries = ["admin", "login"]
const themeName = entries[0]
const staging = await mkdtemp(path.join(tmpdir(), "orchpact-ui-"))
const check = process.argv.includes("--check")

async function bundledNotices(outputs) {
  const directories = new Set(["node_modules/tailwindcss", "node_modules/tw-animate-css"])
  const inputs = Object.values(outputs).flatMap(output => Object.entries(output.inputs))
  for (const [input, { bytesInOutput }] of inputs) {
    if (!bytesInOutput) continue
    const packageRoot = input.match(/^(.*(?:^|\/)node_modules\/(?:@[^/]+\/)?[^/]+)/)?.[1]
    if (packageRoot) directories.add(packageRoot)
  }
  const notices = [await readFile(path.join(root, "LICENSE-shadcn.txt"), "utf8")]
  for (const directory of [...directories].sort()) {
    const location = path.resolve(root, directory)
    const metadata = JSON.parse(await readFile(path.join(location, "package.json"), "utf8"))
    const files = (await readdir(location)).filter(name => /^(license|licence|copying)(?:\b|\.)/i.test(name)).sort()
    const texts = await Promise.all(files.map(name => readFile(path.join(location, name), "utf8")))
    if (!texts.length) {
      // Some npm archives omit their upstream license. Keep the reviewed text locally.
      const fallback = path.join(root, "licenses", metadata.name + "-" + metadata.version + ".txt")
      texts.push(await readFile(fallback, "utf8"))
    }
    notices.push(metadata.name + " " + metadata.version + "\n\n" + texts.join("\n"))
  }
  return notices.join("\n\n----------------------------------------\n\n")
}

try {
  const theme = path.join(staging, "theme.css")
  execFileSync(process.execPath, [
    path.join(root, "node_modules/@tailwindcss/cli/dist/index.mjs"),
    "-i", "src/theme.css", "-o", theme, "--minify",
  ], { cwd: root, stdio: "inherit" })

  const { outputFiles, metafile } = await build({
    absWorkingDir: root,
    entryPoints: entries.map(name => "src/" + name + ".jsx"),
    outdir, bundle: true, write: false, metafile: true, format: "esm",
    splitting: true, chunkNames: "shared-[hash]", jsx: "automatic",
    target: ["es2022"], minify: true, legalComments: "eof",
    external: ["/static/*", "/assets/*"], define: { "process.env.NODE_ENV": '"production"' },
  })
  const files = new Map(outputFiles.map(file => [path.basename(file.path), Buffer.from(file.contents)]))
  const cssName = themeName + ".css"
  files.set(cssName, Buffer.concat([files.get(cssName) ?? Buffer.alloc(0), await readFile(theme)]))
  files.set("LICENSES.txt", Buffer.from(await bundledNotices(metafile.outputs)))

  if (!check) await mkdir(outdir, { recursive: true })
  for (const [name, data] of files) {
    const target = path.join(outdir, name)
    if (check) {
      const existing = await readFile(target)
      if (!existing.equals(data)) throw new Error("Stale bundle: " + target)
    } else {
      await writeFile(target, data)
    }
  }
  for (const name of await readdir(outdir)) {
    if (!/^shared-[A-Z0-9]+\.(js|css)$/.test(name) || files.has(name)) continue
    if (check) throw new Error("Stale UI chunk: " + name)
    await rm(path.join(outdir, name))
  }
  console.log((check ? "Verified" : "Built") + " " + files.size + " local UI assets.")
} finally {
  await rm(staging, { recursive: true, force: true })
}
