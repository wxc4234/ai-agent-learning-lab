import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const venvPython = path.join(
    root,
    ".venv",
    isWindows ? "Scripts/python.exe" : "bin/python",
);

function run(command, args) {
    return spawnSync(command, args, {
        cwd: root,
        stdio: "inherit",
        shell: false,
    });
}

function isAvailable(command, args) {
    const result = spawnSync(command, args, {
        cwd: root,
        stdio: "ignore",
        shell: false,
    });
    return result.status === 0;
}

function createVirtualEnvironment() {
    const candidates = isWindows
        ? [
            ["py", ["-3.12"]],
            ["python", []],
        ]
        : [
            ["python3.12", []],
            ["python3", []],
        ];

    for (const [command, prefixArgs] of candidates) {
        const isPython312 = isAvailable(command, [
            ...prefixArgs,
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)",
        ]);
        if (!isPython312) {
            continue;
        }

        const result = run(command, [...prefixArgs, "-m", "venv", ".venv"]);
        if (result.status === 0 && existsSync(venvPython)) {
            return;
        }
    }

    throw new Error(
        "Unable to create .venv. Install Python 3.12 and ensure it is available on PATH.",
    );
}

if (!existsSync(venvPython)) {
    console.log("Python virtual environment not found; creating .venv with Python 3.12...");
    createVirtualEnvironment();
}

const versionCheck = run(venvPython, [
    "-c",
    "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)",
]);

if (versionCheck.status !== 0) {
    throw new Error("The project .venv must use Python 3.12.");
}

console.log("Synchronizing Python dependencies from requirements.txt...");
let install;

if (isAvailable("uv", ["--version"])) {
    install = run("uv", [
        "pip",
        "install",
        "--cache-dir",
        path.join(os.tmpdir(), "ai-agent-learning-lab-uv-cache"),
        "--python",
        venvPython,
        "-r",
        "requirements.txt",
    ]);
} else {
    if (!isAvailable(venvPython, ["-m", "pip", "--version"])) {
        console.log("pip not found in .venv; bootstrapping it with ensurepip...");
        const ensurePip = run(venvPython, ["-m", "ensurepip", "--upgrade"]);
        if (ensurePip.status !== 0) {
            throw new Error("Unable to bootstrap pip in the project .venv.");
        }
    }

    install = run(venvPython, [
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        "requirements.txt",
    ]);
}

if (install.status !== 0) {
    throw new Error("Python dependency installation failed.");
}
