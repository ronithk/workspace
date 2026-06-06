# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "simple-term-menu",
#   "yaspin",
# ]
# ///

import sys
import os
import subprocess
import shlex
from contextlib import contextmanager
from simple_term_menu import TerminalMenu
import shutil
from yaspin import yaspin

# Command to launch the workspace session (e.g., zellij, tmux, etc.)
WORKSPACE_CMD = []
# Untracked paths to copy into newly created worktrees (relative to CWD).
COPY_UNTRACKED_PATHS = ["DerivedData"]


@contextmanager
def status(text):
    """Display a spinner with status text during an operation."""
    with yaspin(text=text, color="blue", ellipsis="") as sp:
        yield sp


def run_command(cmd, cwd=None, check=True, input=None):
    """Run a shell command and return the result."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd, input=input
    )
    if check and result.returncode != 0:
        print(f"Error running command: {cmd}")
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result


def run_command_stream(cmd, cwd=None, on_output=None):
    """Run a shell command and stream its output line-by-line."""
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
    )
    output = []
    if process.stdout:
        for line in process.stdout:
            output.append(line)
            if on_output:
                on_output(line)
    returncode = process.wait()
    return returncode, "".join(output)


def copy_untracked_paths(source_dir, target_dir):
    for rel_path in COPY_UNTRACKED_PATHS:
        src_path = os.path.join(source_dir, rel_path)
        dst_path = os.path.join(target_dir, rel_path)
        if not os.path.exists(src_path):
            continue
        if os.path.isdir(src_path):
            copy_dir_best_effort(src_path, dst_path)
        else:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            try:
                shutil.copy2(src_path, dst_path)
            except FileNotFoundError:
                # File disappeared between exists check and copy; skip.
                pass


def copy_dir_best_effort(src_dir, dst_dir):
    """Try a fast APFS clone via ditto --clone; skip on failure."""
    if sys.platform != "darwin" or not shutil.which("ditto"):
        print("DerivedData copy skipped: ditto not available.")
        return

    os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
    base_text = "Copying DerivedData"

    def update_spinner(line):
        entry = line.strip()
        if not entry:
            return
        if entry.lower().startswith("copying "):
            entry = entry[len("copying ") :].strip()
            if entry.endswith("..."):
                entry = entry[:-3].rstrip()
            for prefix in (
                "file ",
                "directory ",
                "dir ",
                "folder ",
                "symlink ",
                "link ",
                "special file ",
                "fifo ",
                "socket ",
            ):
                if entry.lower().startswith(prefix):
                    entry = entry[len(prefix) :]
                    break
        idx = entry.find(src_dir)
        if idx != -1:
            entry = entry[idx + len(src_dir) :].lstrip(os.sep)
        entry = os.path.basename(entry.lstrip("./"))
        if entry:
            sp.text = f"{base_text}: {entry}"

    with status(base_text) as sp:
        returncode, output = run_command_stream(
            f"ditto --clone -V {shlex.quote(src_dir)} {shlex.quote(dst_dir)}",
            on_output=update_spinner,
        )
        if returncode != 0:
            sp.fail("DerivedData copy failed")
            if output.strip():
                print(output.strip())
        else:
            sp.text = base_text
            sp.ok("Done")


def get_git_root():
    """Get the root directory of the current git repository."""
    result = run_command("git rev-parse --show-toplevel")
    return result.stdout.strip()




def get_worktree_path(branch_name):
    """Check if a worktree exists for the given branch and return its path."""
    result = run_command("git worktree list --porcelain")
    lines = result.stdout.strip().split("\n")
    
    # Get the main repository path to exclude it
    git_root = get_git_root()

    current_worktree = None
    for line in lines:
        if line.startswith("worktree "):
            current_worktree = line.split(" ", 1)[1]
        elif line.startswith("branch ") and current_worktree:
            branch = line.split(" ", 1)[1]
            if branch == f"refs/heads/{branch_name}":
                # Don't return the main repository as a worktree
                if current_worktree != git_root:
                    return current_worktree
            current_worktree = None

    return None


def get_all_worktrees():
    """Get all worktrees with their branch names."""
    result = run_command("git worktree list --porcelain")
    lines = result.stdout.strip().split("\n")
    
    # Get the main repository path to exclude it
    git_root = get_git_root()

    worktrees = []
    current_worktree = None
    current_path = None
    is_bare = False

    for line in lines:
        if line.startswith("worktree "):
            current_path = line.split(" ", 1)[1]
        elif line.startswith("branch ") and current_path:
            branch = line.split(" ", 1)[1]
            if branch.startswith("refs/heads/"):
                branch_name = branch.replace("refs/heads/", "")
                # Only include actual worktrees, not the main repository
                if current_path != git_root:
                    worktrees.append((branch_name, current_path))
            current_path = None
        elif line == "bare":
            is_bare = True

    return worktrees


def branch_exists(branch_name):
    """Check if a branch exists."""
    result = run_command(
        f"git show-ref --verify --quiet refs/heads/{branch_name}", check=False
    )
    return result.returncode == 0


def main():
    if len(sys.argv) < 2:
        print("Usage: workspace <command> [args]")
        print("Commands:")
        print("  create <branch-name>   Create and switch to a git worktree")
        print("  attach [branch-name]   Attach to an existing worktree")
        print("                         (interactive mode if no branch name given)")
        print("  destroy [branch-name] [--force]  Remove worktree and delete branch")
        print(
            "                                   (interactive mode if no branch name given)"
        )
        print(
            "                                   --force: skip merge check and force delete"
        )
        sys.exit(1)

    command = sys.argv[1]

    if command == "create":
        if len(sys.argv) != 3:
            print("Usage: workspace create <branch-name>")
            sys.exit(1)
        branch_name = sys.argv[2]
        create_worktree(branch_name)
    elif command == "attach":
        if len(sys.argv) > 3:
            print("Usage: workspace attach [branch-name]")
            sys.exit(1)

        if len(sys.argv) == 3:
            # Direct mode with branch name
            branch_name = sys.argv[2]
            attach_worktree(branch_name)
        else:
            # Interactive mode
            attach_worktree_interactive()
    elif command == "destroy":
        force = False
        branch_name = None

        # Parse arguments
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--force":
                force = True
            else:
                if branch_name is None:
                    branch_name = args[i]
                else:
                    print("Error: Too many arguments")
                    print("Usage: workspace destroy [branch-name] [--force]")
                    sys.exit(1)
            i += 1

        if branch_name is None:
            # Interactive mode
            destroy_worktree_interactive(force=force)
        else:
            # Direct mode with branch name
            destroy_worktree(branch_name, force=force)
    else:
        print(f"Unknown command: {command}")
        print("Usage: workspace <command> [args]")
        print("Commands:")
        print("  create <branch-name>   Create and switch to a git worktree")
        print("  attach [branch-name]   Attach to an existing worktree")
        print("                         (interactive mode if no branch name given)")
        print("  destroy [branch-name] [--force]  Remove worktree and delete branch")
        print(
            "                                   (interactive mode if no branch name given)"
        )
        print(
            "                                   --force: skip merge check and force delete"
        )
        sys.exit(1)


def create_worktree(branch_name):
    # Get current directory and git root
    current_dir = os.getcwd()
    git_root = get_git_root()

    # Get the current branch to store as parent
    parent_branch = get_current_branch()

    # Calculate relative path from git root to current directory
    relative_path = os.path.relpath(current_dir, git_root)
    if relative_path == ".":
        relative_path = ""

    # Check if worktree already exists
    worktree_path = get_worktree_path(branch_name)

    created = False
    if worktree_path:
        # Worktree already exists, prompt the user
        print(f"Worktree '{branch_name}' already exists.")
        response = input("Would you like to attach instead? (y/n): ").strip().lower()

        if response != "y":
            print("Operation cancelled.")
            sys.exit(0)
    else:
        # Create branch if it doesn't exist
        if not branch_exists(branch_name):
            with status(f"Creating branch '{branch_name}' from '{parent_branch}'") as sp:
                run_command(f"git checkout -b {branch_name}")
                # Store the parent branch in the description
                set_branch_parent(branch_name, parent_branch)
                run_command("git checkout -")  # Switch back to original branch
                sp.ok("Done")

        # Create worktree inside the worktrees container directory
        parent_dir = os.path.dirname(git_root)
        repo_name = os.path.basename(git_root)
        worktrees_container = os.path.join(parent_dir, f"{repo_name}-worktrees")
        os.makedirs(worktrees_container, exist_ok=True)
        worktree_dir = os.path.join(worktrees_container, branch_name)

        with status(f"Creating worktree at '{worktree_dir}'") as sp:
            run_command(f"git worktree add '{worktree_dir}' '{branch_name}'")
            sp.ok("Done")
        worktree_path = worktree_dir
        created = True

    # Navigate to the worktree directory (and subdirectory if needed)
    target_dir = worktree_path
    if relative_path:
        target_dir = os.path.join(worktree_path, relative_path)
        # Create subdirectory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)

    if created:
        copy_untracked_paths(current_dir, target_dir)

    # Change to the target directory and exec into shell running workspace command
    os.chdir(target_dir)
    with status(f"Launching workspace in '{target_dir}'") as sp:
        sp.ok("Done")
    shell = os.environ.get("SHELL", "/bin/bash")
    workspace_cmd_str = shlex.join(WORKSPACE_CMD)
    os.execvp(shell, [shell, "-c", f"{workspace_cmd_str}; exec {shell}"])


def attach_worktree(branch_name):
    """Attach to an existing worktree for the given branch name."""
    # Get current directory and git root
    current_dir = os.getcwd()
    git_root = get_git_root()

    # Calculate relative path from git root to current directory
    relative_path = os.path.relpath(current_dir, git_root)
    if relative_path == ".":
        relative_path = ""

    # Check if worktree exists
    worktree_path = get_worktree_path(branch_name)

    if not worktree_path:
        print(f"Error: No worktree found for branch '{branch_name}'")
        sys.exit(1)

    # Navigate to the worktree directory (and subdirectory if needed)
    target_dir = worktree_path
    if relative_path:
        target_dir = os.path.join(worktree_path, relative_path)
        # Create subdirectory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)

    # Change to the target directory and exec into shell running workspace command
    os.chdir(target_dir)
    with status(f"Attaching to worktree in '{target_dir}'") as sp:
        sp.ok("Done")
    shell = os.environ.get("SHELL", "/bin/bash")
    workspace_cmd_str = shlex.join(WORKSPACE_CMD)
    os.execvp(shell, [shell, "-c", f"{workspace_cmd_str}; exec {shell}"])


def attach_worktree_interactive():
    """Interactive mode for attaching to worktrees."""
    # Get all worktrees
    worktrees = get_all_worktrees()

    if not worktrees:
        print("No worktrees found to attach to.")
        return

    # Create menu options
    menu_options = []
    for branch_name, path in worktrees:
        menu_options.append(branch_name)

    # Add cancel option
    menu_options.append("Cancel")

    # Show interactive menu
    terminal_menu = TerminalMenu(menu_options, title="Select a worktree to attach to:")
    menu_entry_index = terminal_menu.show()

    # Handle selection
    if menu_entry_index is None or menu_entry_index == len(menu_options) - 1:
        print("Cancelled.")
        return

    # Get selected branch name
    selected_branch = worktrees[menu_entry_index][0]

    # Attach to the selected worktree
    attach_worktree(selected_branch)


def get_current_branch():
    """Get the current branch name."""
    result = run_command("git rev-parse --abbrev-ref HEAD")
    return result.stdout.strip()


def set_branch_parent(branch_name, parent_branch):
    """Set the parent branch in the branch description."""
    description = f"Parent branch: {parent_branch}"
    run_command(f"git config branch.{branch_name}.description '{description}'")


def get_branch_parent(branch_name):
    """Get the parent branch from the branch description."""
    result = run_command(f"git config branch.{branch_name}.description", check=False)
    if result.returncode == 0 and result.stdout.strip():
        description = result.stdout.strip()
        if description.startswith("Parent branch: "):
            return description.replace("Parent branch: ", "").strip()
    return None


def get_main_branch():
    """Try to determine the main branch (main, master, or develop)."""
    for branch in ["main", "master", "develop"]:
        if branch_exists(branch):
            return branch
    # If none of the common main branches exist, use the first branch
    result = run_command("git branch -r | grep -v HEAD | head -1")
    if result.stdout.strip():
        return result.stdout.strip().split("/")[-1]
    return None


def is_branch_merged(branch_name, target_branch):
    """Check if branch_name has been merged into target_branch."""
    # Get the worktree path for the branch
    worktree_path = get_worktree_path(branch_name)

    # Run the command from within the worktree directory
    result = run_command(
        f"git branch --merged {target_branch}", check=False, cwd=worktree_path
    )
    merged_branches = result.stdout.strip().split("\n")
    return any(
        branch.strip().strip("*").strip() == branch_name for branch in merged_branches
    )


def get_remote_branch_tip(branch_name):
    """Get the tip commit hash for origin/branch_name, or None if it doesn't exist."""
    result = run_command(f"git ls-remote --heads origin {branch_name}", check=False)
    output = result.stdout.strip()
    if not output:
        return None
    return output.split()[0]


def is_branch_tip_pushed(branch_name):
    """Check if the local branch tip matches the remote tip."""
    local_tip = run_command(f"git rev-parse {branch_name}", check=False).stdout.strip()
    if not local_tip:
        return False
    remote_tip = get_remote_branch_tip(branch_name)
    if not remote_tip:
        return False
    return local_tip == remote_tip


def destroy_worktree_interactive(force=False):
    """Interactive mode for destroying worktrees."""
    # Get all worktrees
    worktrees = get_all_worktrees()

    if not worktrees:
        print("No worktrees found to destroy.")
        return

    # Create menu options
    menu_options = []
    for branch_name, path in worktrees:
        menu_options.append(branch_name)

    # Add cancel option
    menu_options.append("Cancel")

    # Show interactive menu
    terminal_menu = TerminalMenu(menu_options, title="Select a worktree to destroy:")
    menu_entry_index = terminal_menu.show()

    # Handle selection
    if menu_entry_index is None or menu_entry_index == len(menu_options) - 1:
        print("Cancelled.")
        return

    # Get selected branch name
    selected_branch = worktrees[menu_entry_index][0]

    # Confirm destruction
    print(f"\nSelected: {selected_branch}")
    confirm_menu = TerminalMenu(
        ["Yes, destroy it", "No, cancel"],
        title=f"Are you sure you want to destroy the worktree for '{selected_branch}'?",
    )
    confirm_index = confirm_menu.show()

    if confirm_index == 0:
        destroy_worktree(selected_branch, force=force)
    else:
        print("Cancelled.")


def has_unstaged_changes(cwd=None):
    """Check if there are unstaged changes or untracked files in the working directory."""
    result = run_command("git status --porcelain", check=False, cwd=cwd)
    return bool(result.stdout.strip())




def destroy_worktree(branch_name, force=False):
    # Check if worktree exists
    worktree_path = get_worktree_path(branch_name)
    if not worktree_path:
        print(f"Error: No worktree found for branch '{branch_name}'")
        sys.exit(1)

    if has_unstaged_changes(cwd=worktree_path):
        print(
            f"Error: Worktree for '{branch_name}' has uncommitted changes. "
            "Please commit or discard them before destroying."
        )
        sys.exit(1)

    # Get the parent branch from the description
    parent_branch = get_branch_parent(branch_name)

    # If no parent branch is stored, fall back to main branch detection
    if not parent_branch:
        print(f"Warning: No parent branch information found for '{branch_name}'")
        parent_branch = get_main_branch()
        if not parent_branch:
            print("Error: Could not determine the parent branch")
            print(
                "Please specify the parent branch or merge manually before destroying"
            )
            sys.exit(1)
        print(f"Using '{parent_branch}' as the parent branch")

    if not force:
        with status(f"Checking if '{branch_name}' has been merged or pushed") as sp:
            merged = is_branch_merged(branch_name, parent_branch)
            pushed = is_branch_tip_pushed(branch_name)
            if not merged and not pushed:
                sp.fail("Unmerged changes")
                print(f"Error: Branch '{branch_name}' contains unmerged changes.")
                print(
                    f"Please merge the changes into '{parent_branch}' before destroying the worktree."
                )
                print(
                    "Alternatively, push the latest commit to origin so it is preserved."
                )
                print(f"Or use --force to delete anyway.")
                sys.exit(1)
            sp.ok("Safe to delete")
    else:
        with status("Skipping merge check (--force)") as sp:
            sp.ok("Done")

    # Remove the worktree
    with status(f"Removing worktree at '{worktree_path}'") as sp:
        run_command(f"git worktree remove --force '{worktree_path}'")
        sp.ok("Done")

    # Delete the local branch
    with status(f"Deleting local branch '{branch_name}'") as sp:
        if force:
            run_command(f"git branch -D {branch_name}")
        else:
            run_command(f"git branch -d {branch_name}")
        sp.ok("Done")

    print(f"Successfully destroyed worktree and branch '{branch_name}'")


if __name__ == "__main__":
    main()
