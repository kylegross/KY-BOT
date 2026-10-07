# Checklist design and setup

The Task actions design is implemented locally. No checklist has been created in a
Discord channel and no changes have been pushed. After deployment, register the new
`/create checklist` command once using the project's command-sync setting.

## Proposed appearance

- Compact illustrated gold header, 1600 x 280, with the KY BOT badge at top left.
- Custom checklist title rendered in VONCA Bold within the header.
- Task actions is the selected layout: one inline Manage button beside each task.
- Show a muted italic open-task count directly beneath each colored category heading,
  using singular/plural labels (1 open task / 2 open tasks), updated on completion,
  reopening, creation, deletion and recategorization.
- All-caps category headings use the exact SERVER SETTINGS treatment: VONCA Bold,
  36 px rendered at 2x resolution (18 px displayed), matching tracking, 2 px source
  left clearance and 16 px source top/bottom clearance. Keep the existing section
  spacing and quiet separators beneath the last task in each category.
- Heading color follows the selected artwork finish: gold #E8C47C, dark silver
  #BDC3CA or neon silver #D6E2F3. Gold remains the creation default.
- Match the `/settings` heading-to-first-item gap: place the title MediaGallery
  immediately before the first task Section, preserving the title image's bottom
  clearance and Discord's native component gap. Add no blank TextDisplay, invisible
  separator or extra first-task padding between them, for every finish and category.
- Pastel red, yellow and green priority circles precede High, Medium and Low priority,
  with a visible diameter about 80% of the label text height. Transparent PNG emoji
  assets are kept in `design/assets/branding/checklists/`.
- Each category heading and its tasks form one section, with a separator after its
  last task. Category-management controls sit in a separate section below the tasks.
- A Manage button alongside each task opens private task controls.
- Task-view, category-management and pagination controls sit alongside their labels.
- Compact gold footer with the botanical mark by default; a custom icon/emoji image or
  VONCA lettering can replace its center. Emoji art is an image, not a VONCA glyph.
- Image descriptions are omitted and titles retain mobile-safe left clearance.

## VICE functionality to preserve

Reference: `vice-arena/bot-files/vice-checklist/` in the owner's local VICE repository.

- Authorized messages in the configured checklist channel become tasks, preserving
  source text and attachments before removing the original message.
- Category selection and high/medium/low priority, including uncategorized tasks.
- Category creation, renaming and manual ordering.
- Task completion/reopening, text editing, deletion, recategorization, priority editing,
  manual ordering and explicit category sorting by priority.
- Open/completed views, completion counts and pagination.
- Private task discussion threads seeded with original content and attachments, with
  participant invitations and thread synchronization preserved.
- Completion history and completion-log delivery; deleting a task keeps its thread/history.
- Persistent boards, tasks and control bindings across restarts; stale-edit protection.

KY BOT must configure destinations and authorized roles per server rather than reuse
VICE's hardcoded channel IDs or the literal VICE Admin role name.

## Creation options

`/create checklist checklist_log:Yes|No`: custom title, categories, header/background override, header height
(default 280), footer/background override, footer icon and footer text. Gold artwork is
the default. Yes opens a private channel picker before creating the checklist; No
creates without a log destination. The log channel must be separate and in the same
server. Each board has its own choice, and completions made with logs off are never
forwarded later. Setup supports updating an existing board without losing tasks.

Administrators (Manage Server permission) can create checklists and use task controls.
An optional authorized role can capture messages and manage tasks on that board.
The bot needs View Channel, Send Messages, Embed Links, Attach Files, Manage Messages,
Read Message History, Create Private Threads, Send Messages in Threads and Manage Threads.
Message Content Intent is required for message-to-task capture. Manage Expressions and
available emoji slots let the bot install the three small pastel indicators; standard
circles are used when unavailable. Licensed VONCA fonts must be in KY_BOT_FONT_DIR.

Storage is `ky-checklists.sqlite3`, alongside the configured settings database. It
preserves tasks, categories, artwork, board messages, completion history and retry queues.
The public board uses four entries per page to stay within Discord's Components V2
limit, including lists with a different category for every task. Restarting the bot
restores the saved controls and refreshes existing boards; it never creates a new list.

Task actions is the chosen design. The conversation preview retains Category actions
for comparison and demonstrates private action panels; it does not execute Discord operations.
