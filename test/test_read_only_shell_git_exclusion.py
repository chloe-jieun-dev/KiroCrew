"""Read-only shell auto-approve never runs ``git``.

git reads ``.git/config`` on every invocation, and ``core.fsmonitor`` /
``core.hooksPath`` name a program there that no text classifier sees. So a
read-only ``git status`` still runs whatever that config names. The heartbeat
approver already keeps git out of its unattended auto-approve; the gate's
read-only tier (cron, subagent and app sessions, where nobody may be watching)
and the dashboard's trust-reads mode keep it out the same way.

What is pinned here:

* ``readonly_bash.command_invokes_git`` -- one pure rule for both sites: true
  when any command position runs git (any case, any path, ``.exe``, after a
  ``VAR=`` prefix or a program runner such as ``env``) or the quoting cannot be
  resolved;
* the gate's read-only tier: a git command gets no read-only verdict and falls
  through to the surface's own approval, while other reads still auto-approve;
* the classifier itself is unchanged: ``is_read_only_bash`` still reads a git
  read as read-only, so only the auto-approve sites change.

The trust-reads path is driven end to end in ``test_chat_runner_coverage_approval.py``.
"""

from __future__ import annotations

import pytest

from kiro_crew.hooks import TOOL_ALLOW, TOOL_AUTO_APPROVE, HookManager, ToolCall
from kiro_crew.security.readonly_bash import command_invokes_git, is_read_only_bash

GIT_COMMANDS = [
    "git status",
    "git log --oneline -5",
    "pwd; git status",
    "ls && git diff",
    "ls | GIT log",
    "FOO=1 git status",
    "(git status)",
    "/usr/bin/git status",
    "ls\ngit status",
    "Git branch -a",
    "ls || git status",
    "{ git status; }",
    "env -i git status",
    "timeout 5 git log",
    "ls | xargs git show",
    "C:\\Git\\cmd\\git.exe status",
    "2>/dev/null git status",
    "> out.txt git status",
    "2>&1 git log",
    "echo $(git status)",
    "echo `git status`",
    "cat <(git log)",
    "G=git; $G status",
]

#: Reads that must keep auto-approving after the change.
STILL_APPROVED = [
    "ls -la",
    "cat README.md",
    "head -20 file.py",
    "grep -rn pattern src/",
    "wc -l file.txt",
    "ls | grep digit",
]


@pytest.mark.parametrize("command", GIT_COMMANDS)
def test_every_git_position_is_seen(command: str) -> None:
    assert command_invokes_git(command) is True


@pytest.mark.parametrize(
    "command", STILL_APPROVED + ["echo git", "grep -rn git src/", "cat .gitignore", "ls git/"]
)
def test_git_as_an_argument_is_not_a_git_run(command: str) -> None:
    assert command_invokes_git(command) is False


def test_quoting_that_cannot_be_resolved_counts_as_git() -> None:
    assert command_invokes_git("ls 'unclosed") is True


def _judge(command: str) -> object:
    return HookManager().judge(
        ToolCall(title=f"Running: {command}", is_shell=True, command=command)
    )


@pytest.mark.parametrize("command", GIT_COMMANDS)
def test_the_read_only_tier_does_not_approve_git(command: str) -> None:
    verdict = _judge(command)
    assert verdict.action != TOOL_AUTO_APPROVE, (command, verdict)


@pytest.mark.parametrize("command", ["git status", "git log --oneline -5", "ls && git diff"])
def test_a_git_read_falls_through_to_the_surface_approver(command: str) -> None:
    """Not a deny: the call reaches the surface's own approval, as any other
    unproven call does, so an attended session can still approve it by hand."""
    assert is_read_only_bash(command) is True
    verdict = _judge(command)
    assert (verdict.action, verdict.read_only) == (TOOL_ALLOW, False)


@pytest.mark.parametrize("command", ["git status", "ls && git diff"])
def test_classifier_only_does_not_approve_git_either(command: str) -> None:
    verdict = HookManager().judge(
        ToolCall(title=f"Running: {command}", is_shell=True, command=command),
        classifier_only=True,
    )
    assert verdict.action != TOOL_AUTO_APPROVE


@pytest.mark.parametrize("command", STILL_APPROVED)
def test_other_reads_still_auto_approve(command: str) -> None:
    verdict = _judge(command)
    assert (verdict.action, verdict.read_only) == (TOOL_AUTO_APPROVE, True), (command, verdict)


class _RecordingProvider:
    def __init__(self) -> None:
        self.approved: list[str] = []
        self.rejected: list[str] = []
        self.deny_reasons: list[str] = []

    async def approve_tool(self, request_id: str) -> None:
        self.approved.append(request_id)

    async def reject_tool(self, request_id: str) -> None:
        self.rejected.append(request_id)

    async def steer(self, *_args: object, **_kwargs: object) -> None:
        return None


async def _resolve_headless(
    command: str, approver: object = None, *, shell: bool = True, mcp_server: str = ""
) -> _RecordingProvider:
    """Drive ``_resolve_permission`` as a hook-based surface with no approver
    (a Meetings dispatch) unless *approver* is given.

    ``shell=False`` sends the command the way the DeepSeek harness does: a
    ``bash`` tool streamed as kind ``other``, so no shell command is recovered
    and the command is only the tool's own ``command`` argument."""
    import json
    from unittest.mock import MagicMock, patch

    import kiro_crew.sel as sel_mod
    from kiro_crew import name_grant
    from kiro_crew.llm_helpers import ToolApprovalPolicy, _resolve_permission
    from kiro_crew.providers.base import EVENT_PERMISSION_REQUEST, LLMEvent

    async def _clean(_event: object) -> None:
        return None

    provider = _RecordingProvider()

    async def _record_deny(_provider: object, _event: object, reason: str, **_kw: object) -> None:
        provider.deny_reasons.append(reason)

    import kiro_crew.llm_helpers as llm_helpers_mod

    event = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=f"Running: {command}" if shell else "bash",
        request_id="r1",
        tool_input=json.dumps({"command": command}),
        is_shell=shell,
        tool_kind="execute" if shell else "other",
        raw_tool_params={"command": command},
        mcp_server_name=mcp_server,
    )
    with (
        patch.object(sel_mod, "sel", lambda: MagicMock()),
        patch.object(name_grant, "refusal_for_event", _clean),
        patch.object(llm_helpers_mod, "_steer_host_deny", _record_deny),
    ):
        await _resolve_permission(
            provider,  # type: ignore[arg-type]
            event,
            ToolApprovalPolicy.HOOK_BASED,
            HookManager(),
            on_tool_approval=approver,  # type: ignore[arg-type]
        )
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["git status", "ls && git diff", "git log --oneline -5"])
async def test_a_git_read_is_refused_where_no_approver_exists(command: str) -> None:
    """With no approver the fall-through would reach the default approve and run
    git unchecked; it is refused instead."""
    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_NO_APPROVER_GIT_REASON]


#: The refusal the no-approver git branch sends, so a test can tell it from a hook deny.
_NO_APPROVER_GIT_REASON = (
    "this command runs git, which runs programs its repository config "
    "names, so it is never run without an approval and this surface has no "
    "approver; grant it in the hook's auto_approve_tools to allow it here"
)


def _unreadable_reason(reason: str) -> str:
    """The refusal for a command whose program cannot be read, for *reason*."""
    return (
        f"this command's program cannot be read ({reason}), so it may run git, which "
        "runs programs its repository config names, so it is never run without an "
        "approval and this surface has no approver; grant it in the hook's "
        "auto_approve_tools to allow it here"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    ["git commit -m wip", "git checkout -b topic", "make && git add -A", "env git merge topic"],
)
async def test_a_git_write_is_refused_where_no_approver_exists(command: str) -> None:
    """A git command that is not a read runs repository-configured programs too
    (hooks), so with no approver it is refused like a git read."""
    assert is_read_only_bash(command) is False
    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_NO_APPROVER_GIT_REASON]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["git commit -m wip", "git status", "make && git add -A"])
async def test_a_git_command_sent_as_a_non_shell_kind_is_refused_where_no_approver_exists(
    command: str,
) -> None:
    """A harness that streams its shell tool as kind ``other`` (DeepSeek) gives no
    recovered shell command; the tool's own ``command`` argument is judged instead."""
    provider = await _resolve_headless(command, shell=False)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_NO_APPROVER_GIT_REASON]


@pytest.mark.asyncio
async def test_a_non_git_command_sent_as_a_non_shell_kind_keeps_the_default() -> None:
    provider = await _resolve_headless("touch notes.txt", shell=False)
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
async def test_an_mcp_tool_command_argument_is_not_read_as_shell_text() -> None:
    """A call that names its MCP server is governed by ``@server/tool`` rules; its
    ``command``-named argument is not shell text."""
    provider = await _resolve_headless("git commit -m wip", shell=False, mcp_server="tasks")
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
async def test_a_git_write_still_reaches_an_approver_that_exists() -> None:
    async def _approve(_event: object) -> bool:
        return True

    provider = await _resolve_headless("git commit -m wip", _approve)
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
async def test_a_non_git_write_keeps_the_hook_based_default_where_no_approver_exists() -> None:
    """Only git is refused: a non-git command the hook allows keeps the surface's
    default, so the refusal is scoped to what runs repository-configured programs."""
    provider = await _resolve_headless("touch notes.txt")
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "pytest $(ls tests)",
        "cat <(ls) > out.txt",
        "$EDITOR notes",
        "echo $(git rev-parse HEAD) > out.txt",
        "make `git describe`",
    ],
)
async def test_an_unreadable_program_is_refused_with_its_own_reason(command: str) -> None:
    """A program that cannot be read from the text (an expansion, a substitution)
    may be git, so with no approver it is refused, naming why it cannot be read."""
    from kiro_crew.security.readonly_bash import RUNS_GIT, command_names_git

    reason = command_names_git(command)
    assert reason and reason != RUNS_GIT
    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_unreadable_reason(reason)]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["$EDITOR notes", "/usr/bin/g?t commit -m wip"])
async def test_an_unreadable_program_still_reaches_an_approver_that_exists(command: str) -> None:
    async def _approve(_event: object) -> bool:
        return True

    provider = await _resolve_headless(command, _approve)
    assert (provider.approved, provider.rejected, provider.deny_reasons) == (["r1"], [], [])


@pytest.mark.asyncio
async def test_a_git_read_still_reaches_an_approver_that_exists() -> None:
    async def _approve(_event: object) -> bool:
        return True

    provider = await _resolve_headless("git status", _approve)
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
async def test_a_plain_read_still_auto_approves_where_no_approver_exists() -> None:
    provider = await _resolve_headless("ls -la")
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.parametrize("command", GIT_COMMANDS)
def test_the_heartbeat_approver_refuses_the_same_git_commands(command: str) -> None:
    """The heartbeat approver keeps its own git check; the same table pins that
    it refuses every shape the shared helper refuses."""
    from test_heartbeat_safe_tools import _shell

    from kiro_crew.slack.gateway import _is_heartbeat_safe_tool

    assert not _is_heartbeat_safe_tool(_shell(command))


@pytest.mark.parametrize("command", STILL_APPROVED)
def test_the_heartbeat_approver_still_approves_plain_reads(command: str) -> None:
    from test_heartbeat_safe_tools import _shell

    from kiro_crew.slack.gateway import _is_heartbeat_safe_tool

    assert _is_heartbeat_safe_tool(_shell(command))


#: git as the program by name: plain, by path, in any case (macOS and Windows
#: filesystems are case-insensitive, so ``GIT`` runs git there), with a Windows
#: suffix, and after ``VAR=`` prefixes or a runner.
NAMED_GIT_COMMANDS = [
    "git status",
    "/usr/bin/git status",
    "GIT status",
    "Git.EXE status",
    "ls && GIT_DIR=x git log",
    "xargs git add",
    "timeout 5 git fetch",
    "timeout --signal KILL 5 git fetch",
    "nice -n 5 env -i PATH=/usr/bin git commit -m wip",
    "nice nohup git gc",
    "cmd /c git status",
    "pwsh -Command git status",
    "sh -c 'git commit -m wip'",
    'bash -lc "git commit -m wip"',
    "watch -n5 git fetch",
    "flock /tmp/l git commit -m wip",
    "ionice -c3 git gc",
    "parallel git -C {} pull ::: a b",
]

#: git written with the shell's quoting or escaping, which the shell removes before it
#: looks the program up, or as a Windows path.
QUOTED_GIT_COMMANDS = [
    'g"it" checkout -b topic',
    "g'it' status",
    '"git" add -A',
    "\\git fetch",
    "bash -c 'g\\it checkout -b topic'",
    "eval 'g\\it commit -m wip'",
    "C:\\tools\\git.exe status",
    '"C:\\Program Files\\Git\\bin\\git.exe" status',
    "gi\\\nt commit -m wip",
    "bash -c 'g\\\nit checkout -b topic'",
]

#: git at a command position past a glued redirection, a conditional or loop keyword,
#: or an fd-name redirection.
POSITIONED_GIT_COMMANDS = [
    "git>&2 checkout -b topic",
    "git<msg.txt commit -F -",
    '"git">out.txt commit -m wip',
    "if git checkout -b topic; then echo done; fi",
    "while git fetch; do sleep 1; done",
    "until git pull; do sleep 1; done",
    "coproc git fetch",
    "{fd}>log.txt git commit -m wip",
]

#: git inside a ``case`` arm or a function body, in a runner option that carries a
#: command (``env -S``), after ``rg --pre`` or ``find -exec``, or in an ``eval`` or
#: ``bash -c`` script.
COMPOUND_GIT_COMMANDS = [
    "case x in x) git checkout -b topic;; esac",
    "f() { git commit -m wip; }; f",
    "function f { git commit -m wip; }; f",
    'env -S "git commit -m wip"',
    "env --split-string='git checkout -b topic'",
    "env -S'git commit'",
    "env -iSgit checkout -b topic",
    "env -ivSgit commit -m wip",
    "rg --pre git pattern .",
    "rg --pre=git pattern .",
    "find . -name '*.md' -exec git add {} +",
    "find . -exec ls {} \\; -exec git add {} \\;",
    "eval 'v=1 git commit -m wip'",
    'bash -c "v=1 git commit"',
    "timeout 5 bash -lc 'ls; git commit'",
]

#: After a runner (``xargs``, ``env``, ``nice``) every later word may be the program
#: it runs, since the runner's option values sit before it, so a later word that is
#: exactly ``git`` refuses. The accepted cost; a hook grant lifts it.
RUNNER_ARGUMENT_GIT_COMMANDS = [
    "find . -name '*.md' | xargs grep -ln git",
    "xargs -a files.txt grep -n 'git'",
    "nice -n 10 grep -rn git src/",
    "env LC_ALL=C grep -c git CHANGELOG.md",
]

#: A quoted or oddly-spaced executable path whose basename is git. A no-break space
#: is not a word break for bash either, so ``./echo\u00a0/git`` is one path to ``git``.
QUOTED_PATH_GIT_COMMANDS = [
    '"./echo project/git" commit -m wip',
    '"/usr/bin/echo /git" status',
    "./echo\u00a0/git commit",
]

#: Commands that run git by name: refused with the git reason.
RUNS_GIT_COMMANDS = (
    NAMED_GIT_COMMANDS
    + QUOTED_GIT_COMMANDS
    + POSITIONED_GIT_COMMANDS
    + COMPOUND_GIT_COMMANDS
    + RUNNER_ARGUMENT_GIT_COMMANDS
    + QUOTED_PATH_GIT_COMMANDS
)

#: Programs that cannot be read from the text, so they may be git: a glob, brace list
#: or extglob, an ANSI-C quote or an expansion as the program, a command or process
#: substitution anywhere (single quotes included: ``shlex`` drops the quotes, so a
#: quoted ``$(`` reads like a live one and refuses, the safe side), or quoting that
#: cannot be resolved. Each refuses with its own reason, not the git one.
UNREADABLE_COMMANDS = [
    "/usr/bin/g?t checkout -b t",
    "/usr/bin/[g]it commit -m wip",
    "{g,}it commit -m wip",
    "bash -O extglob -c '/usr/bin/+(gi)t checkout -b topic'",
    "$'\\x67it' commit -m wip",
    "$'git' commit -m wip",
    "${x}t status",
    "$EDITOR notes",
    "G=git; $G status",
    "echo $(git status)",
    "echo $(date)",
    'echo "$(printf ")"; git status)" > out.txt',
    "echo ${a['$(git checkout -b topic)']}",
    "echo `echo \\`git commit -m wip\\``",
    "printf -v 'a[$(git commit -m wip)]' %s foo",
    "echo '$(git log)' > out.txt",
    "cat <(ls) > out.txt",
    "echo 'unterminated",
    "grep.exe -rn git docs\\",
]

#: git only as an argument, or a word that merely contains the letters: the
#: program is not git, so the command keeps the surface's default.
MENTIONS_GIT_COMMANDS = [
    "grep digit README.md",
    "make docs VCS=git",
    "time make docs VCS=git",
    "rg -n git docs/",
    "timeout 60 rg -n git docs/",
    "pytest tests/test_git_helpers.py",
    "C:\\tools\\rg.exe -n git docs",
    "GREP.EXE -c git README.md",
    "findstr /n git README.md",
    "grep -rn git docs/",
    "echo 'use git here'",
    "cat .gitignore",
    "ls git-notes",
    'echo "ordinary; git documentation" > notes.txt',
    "echo 'a | git b' > out.txt",
    'cat "x && git y" > out.txt',
    "grep -c git>count.txt README.md",
    "touch legit.txt digital.txt",
]

#: Commands with no git program, so they keep the surface's default.
KEEPS_DEFAULT_COMMANDS = MENTIONS_GIT_COMMANDS + [
    "echo done>notes.txt",
    "cat a.txt>b.txt",
    "if test -f a.txt; then echo yes>out.txt; fi",
    "pytest -q 2>&1 > out.txt",
    "ls -la",
    "cat README.md",
    "find . -name x -exec touch {} +",
    "echo ${HOME}",
    # A glob, brace list, expansion or JSON payload outside the program position:
    # the program is readable and is not git.
    "rm *.tmp",
    "pytest tests/test_*.py",
    "echo {a,b}",
    'curl -d \'{"a":1,"b":2}\' http://localhost:8000/x',
    "if [ -f a.txt ]; then rm a.txt; fi",
    "xargs -I{} cp {} dst",
    'grep "(foo" file.txt',
]


@pytest.mark.parametrize("command", RUNS_GIT_COMMANDS)
def test_command_names_git_reads_git_as_the_program(command: str) -> None:
    from kiro_crew.security.readonly_bash import RUNS_GIT, command_names_git

    assert command_names_git(command) == RUNS_GIT


@pytest.mark.parametrize("command", UNREADABLE_COMMANDS)
def test_command_names_git_names_why_a_program_cannot_be_read(command: str) -> None:
    from kiro_crew.security.readonly_bash import RUNS_GIT, command_names_git

    reason = command_names_git(command)
    assert reason and reason != RUNS_GIT


@pytest.mark.parametrize("command", KEEPS_DEFAULT_COMMANDS)
def test_command_names_git_passes_a_command_whose_program_is_not_git(command: str) -> None:
    from kiro_crew.security.readonly_bash import command_names_git

    assert command_names_git(command) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("command", RUNS_GIT_COMMANDS)
async def test_a_command_that_runs_git_is_refused_where_no_approver_exists(
    command: str,
) -> None:
    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_NO_APPROVER_GIT_REASON]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", UNREADABLE_COMMANDS)
async def test_a_command_whose_program_cannot_be_read_is_refused_where_no_approver_exists(
    command: str,
) -> None:
    from kiro_crew.security.readonly_bash import command_names_git

    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == ([], ["r1"])
    assert provider.deny_reasons == [_unreadable_reason(command_names_git(command))]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", KEEPS_DEFAULT_COMMANDS)
async def test_a_command_whose_program_is_not_git_keeps_the_hook_based_default(
    command: str,
) -> None:
    provider = await _resolve_headless(command)
    assert (provider.approved, provider.rejected) == (["r1"], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["GIT status", "/usr/bin/git status"])
async def test_git_by_name_still_reaches_an_approver_that_exists(command: str) -> None:
    async def _approve(_event: object) -> bool:
        return True

    provider = await _resolve_headless(command, _approve)
    assert (provider.approved, provider.rejected, provider.deny_reasons) == (["r1"], [], [])


@pytest.mark.timeout(60)
def test_command_names_git_stays_linear() -> None:
    from kiro_crew.security.readonly_bash import RUNS_GIT, command_names_git

    assert command_names_git("echo $(echo " * 20000 + "x" + ")" * 20000)
    assert command_names_git("case x in x) echo hi;; esac; " * 40000) == ""
    assert command_names_git("x>" * 200000) == ""
    assert command_names_git("echo a; " * 40000 + "git status") == RUNS_GIT
    assert command_names_git("xargs " + "a " * 200000) == ""
    assert command_names_git("find . " + "-exec a " * 100000) == ""
    assert command_names_git("env " * 50000 + "ls")  # nesting past the bound refuses
