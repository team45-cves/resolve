# RESOLVE — Testing setup

Build a container with RESOLVE for testing

---

## 0. Environment

1. On the main repo page, click `Code` next to `Add file`
2. Click `Codespaces` then "Create codespace on main"

You can run the following commands in that terminal

---

## 1. Build the image

This command uses Claude but it can use others
From the repo root (where this `Dockerfile` lives, not the dev one):

```bash
docker build --build-arg AI_BACKEND=claude -t resolve:claude .
```

> The Dockerfile defaults to `codex`, so pass `--build-arg AI_BACKEND=claude` for the Claude CLI.
> Valid backends: `codex` | `claude` | `opencode` | `none`.

---

## 2. Grab cJSON (optional)

This is a small open source repo that I found that you can test resolve on. You don't have to use this one specifically.
Clone it into your current folder so the container can see it:

```bash
git clone https://github.com/DaveGamble/cJSON
```

You should now have a `cJSON/` folder next to the Dockerfile.

---

## 3. Run the container

```bash
docker run -it -v claude-auth:/root/.claude -v "${PWD}:/work" -w /work resolve:claude bash
```

You'll land at a `root@...:/work#` prompt. `ls` should show `cJSON/` if that's the repo you are using.

---

## 4. Log in to Claude (one time per auth volume)

Inside the container:

```bash
claude
```
1. Choose **"Claude account with subscription"**
2. Open the printed **URL** in your own laptop browser (Ctrl/Cmd-click or copy-paste the full URL).
3. Approve, copy the **code** it shows, paste it back at the terminal prompt, Enter.
4. Type `/exit` to leave the Claude session.

---

## 5. List the RESOLVE commands

```bash
resolve help
```

Now you can run resolve. 
