# Ticketing

Open **Command Center → TICKETS** or run **`/ticket setup`**. Setup requires
Administrator. Both entry points edit the same guild settings and use the existing
inline Components V2 menus, silver artwork, category strips and Home/Back routing.
The slash-command setup response is ephemeral. Public ticket controls survive restarts.

For a new panel created through `/ticket setup`, its initial panel channel is the
server text channel where the command was run. **Panel Channel → Choose channel(s)**
remains editable, so the administrator can choose another channel before publishing.
Opening setup never moves an existing panel or replaces its saved channel. Command
Center setup starts with an explicit channel selection instead of assuming that the
Command Center channel should host the public panel.

Each selected panel has **Channel Settings** for its own support team, access rules,
ticket category, log/transcript destinations, messages, limits and automation. These
controls are available from both setup entry points.

**Panel → Staff & Approval Roles** is a direct shortcut to support roles, ticket
approvers and ticket managers. Support staff handle ordinary ticket operations.
Approvers review requests; managers can also approve, assign and transfer tickets.
The optional **Require Ticket Approval** toggle creates tickets in a waiting state
with the opener's replies disabled. Configured approvers or managers can approve
the request or decline it with a reason. Approval is off by default. Pending requests
count against opening limits, and ordinary messages/status changes cannot approve them.

**Panel → Appearance** configures header and footer artwork independently. Choose a
standard KY BOT design and one of its four existing finishes, upload your own still
PNG/JPEG/WebP image, or hide either banner. Custom uploads are normalized and persisted
in the bot database rather than depending on temporary Discord image URLs. Each upload
may be up to 8 MB before processing. Preview and publication attach the selected images,
and updating a panel replaces its prior artwork. Standard header titles are not repeated
as another text heading below the artwork; setup menus also omit their duplicate title.

## Initial setup

1. In **Shared Defaults → Staff & Access**, choose support and ticket-manager roles.
2. In **Shared Defaults → Channels & Logs**, choose the ticket category, one or more
   activity log channels, and one or more transcript channels. Logs must be staff-only.
3. Create ticket types such as Support, Member Reports and Appeals. Each type can
   have up to five required intake questions and independent staff/access, category,
   logs, transcript, workflow, messages and automation overrides.
4. Create a panel, select its public channel and offered ticket types, customize its
   title, description, accent color, image and footer, then preview and publish it.
5. Enable ticketing from the TICKETS home menu. Publish additional panels into other
   channels as needed. Publishing updates the saved message instead of duplicating it.

**Panel → Ticket Types** presents a checkbox-style topic list. Starter choices are
Support, Member Reports, Appeals, Applications and Other; selecting one creates the
topic and adds it to that panel. Use **Add Topic** for a custom choice. Every toggle
saves immediately, other pages' selections are preserved, and the menu shows how
many topics are selected. Go Back → Review to preview and publish afterward.

Up to 100 panels and 100 types can be configured per server. A panel offers up to
25 types; small panels use inline buttons and larger panels use compact button rows.
Each log setting accepts up to 10 destinations. A type can appear on several panels.

## Routing

For newly opened tickets: **shared defaults → panel overrides → type overrides**.
An explicit empty selection overrides rather than inherits. Reset Defaults removes
overrides. Activity log and transcript destinations are separate, and both fan out
to all selected channels. Different ticket types can use different teams and private
log channels; a shared central log can be visible to configured ticket staff.

Existing tickets retain a snapshot of their staff, automation and destination settings.
Transferring a ticket explicitly applies its destination type's current configuration.
Only ticket managers with access to the destination team can transfer tickets.

## Log viewer roles

**Access & Privacy → Log Access** controls log visibility independently of support
and approval roles. The default follows **Ticket Managers**; support roles do not
automatically receive access to activity logs or transcripts. Choose **Log Viewer
Roles** for a separate audience, **Use Managers** to follow manager roles, or clear
the role picker for administrators only.

After choosing the activity/transcript destinations and viewer roles, use **Apply
Channel Access**. The menu lists the channels it will update. KY BOT hides those
channels from @everyone, removes other role/member viewing overrides, grants the
selected roles viewing and message-history access, and retains the bot's delivery
permissions. It preserves unrelated permission settings. The bot needs Manage
Permissions in each destination. Ticket and public-panel channels are rejected;
their access is not changed by this action.

A channel shared by multiple configured panels/types uses their combined log viewer
roles. Use different destinations when those audiences must remain separate.
Discord administrators always bypass channel restrictions. Unselected roles or
direct member overrides cause delivery to wait until channel access is corrected.
Selecting destination channels is allowed before applying access, but publishing
and log delivery still check privacy before posting any records.

## Handling tickets

Members press a panel button, complete its form, and receive a private channel link.
Tickets are visible to the opener, configured support/managers, added participants,
and Discord administrators. Opening limits apply across the member's active tickets
in that server; cooldowns and allow/block roles are checked again at submission.

Use the ticket buttons or **`/ticket manage`** inside a ticket channel. Staff can
claim/unclaim, assign, rename, transfer, set priority, add/remove participants, use
saved replies, optionally notify support roles, keep private staff notes, and pause automation. Notes are shown only
in ephemeral staff controls and are excluded from member transcripts.

Status tracks whether a member or staff reply is needed. The queue shows active,
unclaimed and assigned tickets plus closure counts, average first response / resolution times and support ratings. Staff can
request member confirmation before closing. Optional ratings accept one response
from the opener. Only staff reopen tickets. Closing requires a reason and confirmation;
deletion is a separate staff action with its own confirmation.

## Automation and recovery

- Configurable daily support hours use an IANA timezone; 0–24 permits all-day opening.
- Inactivity reminders and closure run only while waiting for the member. The bot
  always warns before automatic closure and allows at least an hour to respond.
- Overdue staff replies generate an activity-log alert. Per-ticket pause/resume is
  available; resuming restarts the timers.
- SQLite and PostgreSQL store settings, tickets, archives and per-destination events.
  Failed log delivery retries without recreating tickets. A failed destination does
  not stop other destinations. Delivery is at least once; an interruption after send
  but before acknowledgement can produce a duplicate log entry.
- Incomplete channel creation is reconciled using the ticket token in its channel
  topic. Failed introductions retry. Manually removed channels release active slots.
- Private ticket channels are excluded from the generic server event logger, including
  message edits/deletions and child threads. Independently configured third-party bots
  or other channel automation remain subject to their own settings.

## Transcripts and retention

Closing first saves an escaped HTML snapshot of current messages, authors, timestamps,
embeds and attachment links. It does not reconstruct deleted messages, edit history,
or download attachments; Discord attachment URLs may expire. The opener, participants
and configured staff can export a ticket they can access. Optional DM copies can fail
when the member blocks DMs; the staff archive remains available.

Before deletion, the bot creates a fresh snapshot and requires successful delivery to
every configured transcript destination. Export, upload or permission failures preserve
the ticket channel. Very large transcripts exceeding the upload budget require a
separate export and are never silently truncated or automatically deleted.

Database archives expire after their ticket's configured retention (30 days by default,
checked daily). Closed/deleted tickets' stored intake answers, notes and closure reasons
are cleared with archive expiry. Pending delivery postpones expiry. Ticket identifiers,
ownership, timestamps, settings and ratings remain for routing and operational records.
Exports already posted to Discord, sent by DM or downloaded are independent copies;
database retention does not remove them or the Discord ticket channel.

## Deployment

Install updated dependencies (including `tzdata` for Windows support-hour timezones),
restart KY BOT and sync its commands once to expose `/ticket setup` and `/ticket manage`.
The feature starts disabled. Configure and publish a test panel before enabling it.
KY BOT needs Manage Channels and Manage Permissions in the ticket category, and View
Channel, Send Messages, Embed Links, Read Message History and Attach Files in ticket
and logging destinations. Administrator permission for the bot is unnecessary.

Advanced conditional intake routing, a searchable knowledge base, message-context
ticket creation and AI assistance are future extensions, not active features.
