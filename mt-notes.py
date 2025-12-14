from textual.app import App
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Static, Input, OptionList, DirectoryTree, Footer, LoadingIndicator, Button, TextArea
from textual.widget import Widget
from textual.widgets.option_list import Option
from textual.events import Key
from textual.widgets import _tree
from textual.binding import Binding
import os
import sys
import logging
import pyperclip
import keyring
import webbrowser
import threading
import requests
import time
import base64
logging.basicConfig(
    filename="mt-notes.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
CLIENT_ID = "Ov23liDYJ6NPGl5nG7Pv"  # Replace with your GitHub OAuth app client ID
SCOPE = "repo"  # adjust as needed

service_name = "mt-notes"
username = "user"
class NotesApp(App):
    CSS_PATH="mt.css"
    def code_update(self, text):
        self.call_from_thread(self.code_display.update, text)

    def status_update(self, text):
        self.call_from_thread(self.status.update, text)
    async def _mount_copy_coroutine(self):
        logging.info("testing")
        await self.mount(Horizontal(self.copy_code, classes="btn-container"))
        self.copy_code.focus()

    def mount_copy_button(self):
        self.call_from_thread(lambda: self.call_later(self._mount_copy_coroutine))
    def github_device_flow(self):
        # Step 1: request device and user codes
        try:
            res = requests.post(
                "https://github.com/login/device/code",
                data={"client_id": CLIENT_ID, "scope": SCOPE},
                headers={"Accept": "application/json"},
                timeout=10,
            )
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            self.status_update(f"Error requesting device code: {e}")
            return

        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        interval = data.get("interval", 5)
        self.user_code = user_code
        self.code_update(f"Go to: {verification_uri}\nEnter code: {user_code}")
        self.mount_copy_button()
        webbrowser.open(verification_uri)
        self.status_update("Waiting for approval...")

        # Step 2: poll for token
        while True:
            try:
                token_res = requests.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                token_res.raise_for_status()
                token_data = token_res.json()
            except Exception as e:
                self.status_update(f"Error polling token: {e}")
                return

            if "error" in token_data:
                if token_data["error"] == "authorization_pending":
                    time.sleep(interval)
                    continue
                elif token_data["error"] == "slow_down":
                    interval += 5
                    time.sleep(interval)
                    continue
                elif token_data["error"] == "access_denied":
                    self.status_update("User denied access.")
                    return
                else:
                    self.status_update(f"Auth error: {token_data}")
                    return

            # Got token
            self.token = token_data["access_token"]
            keyring.set_password("mt-notes", "user", self.token)
            # self.status_update(f"Login complete! Access token:\n{self.token}")
            self.call_from_thread(lambda: self.call_later(self.action_restart))
            return 
    async def delete_file(self, owner, repo, path, branch="main", token=None, message="Delete file"):
        if not token:
            raise ValueError("GitHub token required to delete files")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        # 1️⃣ Get file info to obtain SHA
        info_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        r = requests.get(info_url, headers=headers)
        r.raise_for_status()

        sha = r.json()["sha"]

        # 2️⃣ Delete file
        delete_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        payload = {
            "message": message,
            "sha": sha,
            "branch": branch
        }

        r = requests.delete(delete_url, headers=headers, json=payload)
        r.raise_for_status()

        return True
    async def read_file(self, token, owner, repo, path, branch="main"):
        headers = {
            "Accept": "application/vnd.github.raw"
        }

        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        logging.info(r.text)
        return r.text
    async def get_files(self, owner, repo, branch="main", token=None):
        headers = {
            "Accept": "application/vnd.github+json"
        }

        if token:
            headers["Authorization"] = f"Bearer {token}"

        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        r = requests.get(tree_url, headers=headers)
        r.raise_for_status()

        tree = r.json().get("tree", [])

        files = {}

        for item in tree:
            if item.get("type") != "blob":
                continue
            logging.info(item)
            path = item["path"]
            name = os.path.basename(path)

            api_download_url = (
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
                f"?ref={branch}"
            )

            files[name] = api_download_url

        return files
    async def create_file(self,repo: str, file_path: str, content: str, commit_message: str, token: str, owner: str, committer_name="Your Name", committer_email="you@example.com"):
        """
        Create a file in a GitHub repo, using GitHub REST API.
        
        Args:
            repo (str): Repository name.
            file_path (str): Path of the file in the repo.
            content (str): File content as plain text.
            commit_message (str): Commit message.
            token (str): GitHub access token.
            owner (str): Repo owner.
            committer_name (str): Name of the committer.
            committer_email (str): Email of the committer.
        
        Returns:
            int: HTTP status code of the API response.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        data = {
            "message": commit_message,
            "content": encoded_content,
            "committer": {
                "name": committer_name,
                "email": committer_email
            }
        }

        res = requests.put(url, headers=headers, json=data)
        logging.info(f"{res.status_code}: {res.text}")
        return res.status_code
    async def check_file(self, path: str, repo: str, owner: str, token: str) -> bool:
        """
        Check if a file exists in a GitHub repository.

        Args:
            path (str): Path of the file in the repository (e.g., "mt-notes").
            repo (str): Name of the repository.
            owner (str): Owner of the repository.
            token (str): GitHub access token with 'repo' scope.

        Returns:
            bool: True if the file exists, False otherwise.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return True
        elif res.status_code == 404:
            return False
        else:
            # Optional: log or raise for other errors
            raise Exception(f"GitHub API returned status {res.status_code}: {res.text}")
        
    async def refresh_files(self, file_list: OptionList=None):
        if file_list:
            file_list.remove()
        try:
            if self.select_instruction:
                self.select_instruction.remove()
        except:
            pass
        self.select_instruction = Static("Select note to edit", classes="instruction")
        self.mount(self.select_instruction)
        files = await self.get_files(self.logged_in_as, self.repo, "main", self.token)
        self.files = files
        logging.info(str(files))
        if len(files) == 1:
            self.mount(Static("You have no notes saved. Create one with the button below or by pressing ^n", id="no_notes"))
        else:
            file_names = []
            for file in files:
                logging.info(file)
                if file == "mt-notes":
                    continue
                file_names.append(Option(file[:-3]))
            self.file_list = OptionList(*file_names, classes="file_list")
            self.mount(self.file_list)
            self.file_list.focus()
    async def open_file(self, file_path):
        self.file_path = file_path
        self.file_saved = True
        self.file_list.remove()
        if self.file_saved:
            self.select_instruction.update(file_path)
        else:
            self.select_instruction.update(str(file_path) + "*")
        self.mount(self.file_textarea)
        self.file_textarea.focus()
        logging.info(file_path)
        contents = await self.read_file(self.token, self.logged_in_as, self.repo, file_path)
        self.original_contents = contents
        self.file_textarea.text=contents
    async def save_file(self, owner, repo, path, content, branch="main", token=None, message="Update file"):
        """
        Save a file to GitHub (create or update).
        
        :param owner: repo owner
        :param repo: repo name
        :param path: file path in repo (e.g. "folder/file.txt")
        :param content: file content as string
        :param branch: branch name
        :param token: GitHub access token (required)
        :param message: commit message
        """
        if not token:
            raise ValueError("GitHub token required")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        # Encode content in base64
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        # Check if the file already exists to get its SHA
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        r = requests.get(url, headers=headers)

        data = {
            "message": message,
            "content": encoded_content,
            "branch": branch
        }

        if r.status_code == 200:
            # File exists → include SHA to update
            sha = r.json()["sha"]
            data["sha"] = sha

        # PUT request to create or update
        r = requests.put(url, headers=headers, json=data)
        r.raise_for_status()

        return r.json()  # returns commit info
    async def on_text_area_changed(self, event: TextArea.Changed):
        self.file_saved = False
        file_path = self.file_path
        self.select_instruction.update(f"{file_path[:-3]}" if self.file_saved else f"{file_path[:-3]}*")
    async def close_file(self):
        self.file_textarea.text=""
        self.file_textarea.remove()
    async def action_save_file(self):
        await self.save_file(self.logged_in_as, self.repo, self.file_path, self.file_textarea.text, "main", self.token)
        self.file_saved = True
        self.select_instruction.update(self.file_path[:-3])
    async def action_go_home(self):
        await self.home([self.file_textarea])
    async def action_restart(self):
        script_dir = os.path.dirname(os.path.realpath(__file__))
        """An action to restart the app."""
        # Optional: log restart
        logging.info("Restarting..")
        python = sys.executable  # Path to python
        os.execv(python, [python] + sys.argv)  # replace process
    BINDINGS = [
            Binding(key="ctrl+q", action="quit", description="Quit the app", key_display="^q"),
            Binding(
                key="ctrl+r",
                action="restart",
                description="Restart",
                key_display="^r",
            ),
            Binding(key="ctrl+n", action="new_file", description="New file", key_display="^n"),
            Binding(key="ctrl+s", action="save_file", description="Save the file opened", key_display="^s"),
            Binding(key="delete", action="delete_file", description="Delete highlighted file", key_display="del"),
            Binding(key="ctrl+m", action="go_home", description="Main menu", key_display="^m"),
            Binding(key="ctrl+l", action="logout", description="Logout", key_display="^l")
    ]
    async def on_mount(self) -> None:
        logging.info("App loaded")
        # define all components

        token = keyring.get_password(service_name, username)
    
        if token:
            logged_in = True
            self.token = token
        else:
            logged_in = False
        self.title_element = Static("""
                                            
                                            
          █▄                  █▄            
 ▄       ▄██▄     ▄          ▄██▄           
 ███▄███▄ ██      ████▄ ▄███▄ ██ ▄█▀█▄ ▄██▀█
 ██ ██ ██ ██ ▀▀▀▀ ██ ██ ██ ██ ██ ██▄█▀ ▀███▄
▄██ ██ ▀█▄██     ▄██ ▀█▄▀███▀▄██▄▀█▄▄▄█▄▄██▀
                                   by mystyy      
                                            



""", id="title")
        self.mount(self.title_element)
        logging.info(logged_in)
        if not logged_in:
            self.login_prompt = Static("Login with github to start taking notes!", classes="h2")
            self.auth_button = Button("Login", id="login-btn")
            self.code_display = Static("", id="code-display")
            self.status = Static("", id="status")
            self.mount(self.login_prompt, self.code_display, self.status)
            self.mount(Horizontal(self.auth_button, classes="btn-container"))
            self.copy_code = Button("Copy code", id="copy-btn")
        else:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json"
            }
            res = requests.get("https://api.github.com/user", headers=headers)
            logged_in_as = res.json()["login"]
            self.logged_in_as = logged_in_as
            self.username = Static(f"Logged in as: {logged_in_as}", id="username")
            self.mount(self.username)
            self.instruction = Static("Please select the repo you wish to store notes (recommended private)", id="instruction")
            self.mount(self.instruction)
            self.select_repo = Horizontal(Input(placeholder="my-notes-repo", id="repo-select-input"), id="repo-select-container")
            self.mount(self.select_repo)
    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id=="repo-select-input":
            logging.info("Repo selected: " + event.input.value)
            self.repo = event.input.value
            url = f"https://api.github.com/repos/{self.logged_in_as}/{event.input.value}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            }
            res = requests.get(url, headers=headers)
            logging.info(res.status_code, res.content)
            if res.status_code != 200:
                self.instruction.update("That repo doesn't exist! Pick a different one.")
            if res.status_code == 200:
                if await self.check_file("mt-notes", self.repo, self.logged_in_as, self.token):
                    await self.home([self.instruction, self.select_repo])
                else:
                    self.instruction.update("Repo will be wiped. Are you sure?")
                    options = [
                        Option("Yes", id="yes"),
                        Option("No", id="no")
                    ]
                    self.confirm = OptionList(*options, id="confirm-box")
                    self.mount(self.confirm)
                    self.confirm.focus()
        if event.input.id == "file_name_input":
            file_name = event.input.value
            await self.create_file(self.repo, file_name+".mt", "", f"added new note called {file_name}", self.token, self.logged_in_as)
            await self.refresh_files(self.file_list)
            self.file_name_input.remove()
    async def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id == "confirm-box":
            if event.option.id == "no":
                self.confirm.remove()
                self.select_repo.focus()
            elif event.option.id == "yes":
                # wipe the repo
                repo_name = self.select_repo.value
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                }
                self.headers = headers
                # Get default branch
                self.repo_url = f"https://api.github.com/repos/{self.logged_in_as}/{repo_name}"
                res_repo = requests.get(self.repo_url, headers=headers)
                if res_repo.status_code != 200:
                    self.instruction.update("Failed to fetch repo info!")
                    return
                default_branch = res_repo.json().get("default_branch", "main")

                async def delete_recursive(path=""):
                    url = f"https://api.github.com/repos/{self.logged_in_as}/{repo_name}/contents/{path}"
                    res_files = requests.get(url, headers=headers, params={"ref": default_branch})
                    if res_files.status_code != 200:
                        self.instruction.update(f"Failed to list {path or 'root'} contents")
                        return
                    files = res_files.json()
                    if isinstance(files, dict):
                        # Single file
                        files = [files]
                    for file in files:
                        if file["type"] == "dir":
                            await delete_recursive(file["path"])
                        elif file["type"] == "file":
                            sha = file["sha"]
                            del_res = requests.delete(
                                f"https://api.github.com/repos/{self.logged_in_as}/{repo_name}/contents/{file['path']}",
                                headers=headers,
                                json={"message": "Wipe repo via mt-notes", "sha": sha, "branch": default_branch},
                            )
                            if del_res.status_code in (200, 201):
                                self.instruction.update(f"Deleted {file['path']}")
                            else:
                                self.instruction.update(f"Failed to delete {file['path']}: {del_res.status_code}")

                self.confirm.remove()
                self.instruction.update(f"Wiping repo {repo_name}...")
                await delete_recursive()
                self.instruction.update(f"Repo {repo_name} wiped successfully!")
                await self.create_file(self.repo, "mt-notes", " ", "made repo a mt-notes repo", self.token, self.logged_in_as)

                await self.home([self.instruction, self.select_repo])
        elif "file_list" in event.option_list.classes:
            logging.info(f"Opening {event.option.prompt}")
            file_to_open = str(event.option.prompt) + ".mt"
            logging.info(self.files)
            url = self.files[file_to_open]
            await self.open_file(file_to_open)
    async def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        if  "file_list" in event.option_list.classes:
            self.file_highlighted = event.option.prompt
    async def home(self, widgets_to_hide=[]):
        for widget in widgets_to_hide:
            if isinstance(widget, Widget):
                widget.remove()
        logging.info("Loading Home page...")

        await self.refresh_files()
        self.file_name_input = Input(placeholder="Name your note...", id="file_name_input")
        self.file_textarea = TextArea()
            
        # option list of all files under {repo_url}/notes
        # once user picks a file go to notes function
        # takes in file url
        # retrieves file content and displays it 
        # find a widget that lets u edit files basically
        # use textarea
        # set up keybinds like ctrl+s to save and push to github
        # maybe set up vim keybinds
        # css to make it look good pleaseseeeee
        # nearly done!
            
        




    async def action_logout(self):
        try:
            keyring.delete_password(service_name, username)
        except Exception as e:
            logging.info(f"Failed to delete token from keyring: {e}")
        await self.action_restart()

    async def action_new_file(self):
        self.mount(self.file_name_input)
        self.file_name_input.focus()
    async def action_delete_file(self):
        await self.delete_file(self.logged_in_as, self.repo, str(self.file_highlighted)+".mt", "main", self.token)
        await self.refresh_files(self.file_list)
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "login-btn":
            self.auth_button.remove()
            # self.auth_button.disabled = True
            threading.Thread(target=self.github_device_flow, daemon=True).start()
        if event.button.id == "copy-btn":
            pyperclip.copy(self.user_code)

            # self.device_code = Static(device_code)
    def compose(self):
        yield Footer()
if __name__ == "__main__":
    NotesApp().run()