# RentHop home runner (Mac)

RentHop blocks cloud servers, so this part runs on a Mac at home and uploads what it
finds to the repo. The hourly GitHub job merges it into the board. It makes an ordinary
web request from the home connection; if RentHop still serves a bot check, it reports
"blocked" and does not try to get around it.

## Setup (once, about ten minutes)

Open Terminal and paste these one at a time.

1. Install Homebrew if you don't have it (skip if `brew` already works):

       /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

2. Install Python, git and the GitHub CLI:

       brew install python git gh

3. Log in to GitHub (choose GitHub.com, HTTPS, then "Login with a web browser"):

       gh auth login

4. Get the code and run the installer:

       cd ~ && git clone https://github.com/jackmclintock/nyc-studio-finder.git && cd nyc-studio-finder && bash local/install_mac.sh

It runs immediately, then every hour while the Mac is awake, and catches up after sleep.
Results appear on the board within an hour under the RentHop source.

## Checking on it

- Log: `~/Library/Logs/nyc-studio-finder.log` (and `local/renthop_local.log` in the repo folder)
- Run by hand: `cd ~/nyc-studio-finder && python3 -m local.renthop_local --dry-run`
- If the log says it found no listings, send `local/last_renthop.html` to Claude so the
  parser can be adjusted to what RentHop actually serves.
- If the log says BLOCKED, RentHop is refusing home connections too and this route is closed.
- Update the code later with `cd ~/nyc-studio-finder && git pull`
- Remove the schedule: `bash local/uninstall_mac.sh`
