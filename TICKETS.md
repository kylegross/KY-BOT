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
private-thread behavior, log/transcript destinations, messages, limits and automation. These
controls are available from both setup entry points.

**Panel → Staff & Approval Roles** is a direct shortcut to support roles, ticket
approvers and ticket managers. Support staff handle ordinary ticket operations.
Approvers review requests; managers can also approve, assign and transfer tickets.
The optional **Require Ticket Approval** toggle creates tickets in a waiting state
with access limited to staff until approved. Configured approvers or managers can approve
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
2. In **Shared Defaults → Channels & Logs**, choose one or more
   activity log channels, and one or more transcript channels. Logs must be staff-only.
3. Create ticket types such as Support, Member Reports and Appeals. Each type can
   have up to five required intake questions and independent staff/access,
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
25 types in inline sections, with five topics per page so descriptions and styled
headings remain visible.
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
KY BOT needs Create Private Threads and Manage Threads in the panel channel, plus
View Channel, Send Messages, Send Messages in Threads, Embed Links, Read Message
History and Attach Files. Existing legacy channel tickets still require their original
channel-management permissions. Log destinations retain their configured access rules.
Administrator permission for the bot is unnecessary.

Advanced conditional intake routing, a searchable knowledge base, message-context
ticket creation and AI assistance are future extensions, not active features.

## Editable panel artwork and topic sections

### Headers and additional text sections

Under **Panel → Panel Text**, configure **Before the Buttons** and **After the
Buttons** independently. Each main text area has an optional header above its body;
adding that header does not add a divider. Header and body formatting are separate.

Use **Add Section** to add an extra header-and-text block to either area, then choose
it from the section picker. Extra sections have a **Section Divider** toggle, so the
rule can be shown or hidden. You can add up to three extra sections on each side and
remove them individually. Empty sections do not leave stray dividers or empty text.

Formatting supports the same KY BOT special lettering and Discord text styles. The
finish/color controls apply to the selected section. Additional body text supports
up to 500 characters per section; decorative text uses one line of up to 80 characters.
Preview checks Discord's component, image and text limits and adjusts topic paging;
very complex layouts may require fewer image headings or shorter sections. Existing
text is preserved, and draft edits appear publicly only after republishing the panel.

**Panel → Panel Text** adds optional text before and/or after the ticket buttons.
Each block has its own text, formatting, finish and color. Choices match the custom
embed tools: normal text, headings, bold/italic, subtext, Discord formatting, Unicode
lettering, and KY BOT special lettering. Blank text removes a block. Existing panels'
introductory descriptions continue to appear until a custom before-text block is set.
KY BOT special text uses a single line of up to 80 characters; other styles allow
up to 1500 characters before the buttons and 1000 afterward. Preview both blocks before
publishing. Topic pages adjust to keep the text and images within Discord's limits.

In **Panel → Appearance → Header Artwork**, use **Edit Header Text** to change the
lettering inside the header image independently of the panel name. Blank text removes
that lettering. The same title tool can overlay text on an uploaded header background.

In **Footer Artwork → Footer Content**, choose the default design, text inside the
footer, or your own icon. Text is editable; icons can be uploaded/replaced using a
transparent PNG or WebP. The header and footer retain their independent image-source
and finish settings. A caption below the footer remains a separate setting under
Color & Content. Empty captions no longer produce a bare `-#` marker.

In **Ticket Types → Customize Selected Topics**, choose Applications, Appeals or
another selected topic. **Section & Button Text** edits its section heading,
description, button label and button emoji independently. **Heading Format** offers
KY BOT special headings, large/regular headings, bold, italic, underline, Unicode
lettering and custom Discord formatting. KY BOT special headings use the same
rendered lettering and finishes as custom embeds, with an optional custom color.
**Description Format** includes normal text, small muted subtext and Discord styles.
The topic's internal name and routing remain separate from its public text.

Larger panels page through five inline sections at a time. Header/footer and special
heading images are included on each page; private previews allow paging while ticket
opening stays disabled. Public paging preserves the published appearance, so draft
edits do not become public until the panel is published again. Editing a shared ticket
type affects each panel using it when that panel is republished.


### Opened ticket panel

In **Tickets → Ticket Panels → choose a panel → Opened Ticket Panel**, edit the
message shown inside each new private ticket thread. The same editor is available through
`/ticket setup`. Message Sections provides optional headings, independently styled
text, and up to three additional sections on each side of the controls, each with
an optional divider. Appearance provides the standard KY BOT artwork or custom
uploads, editable header lettering, footer text/icons, accent color and body image.
Private Preview shows the layout, a sample submitted message, and disabled buttons.

New tickets open as private threads beneath the channel containing the published
ticket panel. No new text channel is created. The member selects a topic,
completes any intake form, and receives a link to the private channel. Required
approval keeps the thread staff-only until approved. Intake answers appear inside
the opening panel after the welcome text and a divider. Staff claim and respond, then close with a reason and confirmation;
transcripts and activity go to their configured destinations.

Standard opening controls are Claim/Unclaim, Close, Transcript and Manage.
Pending requests show Approve/Decline; closed tickets show Reopen. Manage contains
staff notes, priority/status, saved replies, pause/resume automation, request-close,
participants, assignment, renaming and transfer. Delete is available to authorized
staff after closure, with transcript and confirmation requirements. Permission checks
still apply even when a member can see a staff button.

Opening-panel edits save for newly created tickets immediately; existing tickets
retain their saved design. These edits do not republish the public ticket-opening
panel. Claim, approval, closure and reopen refresh the original opening panel's
status and controls while retaining its artwork. Existing legacy ticket messages
continue to use their original controls.


#### Opening details, buttons and submitted messages

**Opened Ticket Panel → Details & Buttons** edits the title/status lines, approval
notice, submitted-message identity, per-line formatting, status labels and each
standard button's label, emoji and color. Available placeholders are `{type}`,
`{number}`, `{username}`, `{user_id}`, `{owner}`, `{status}` and `{assignee}`.
Blank templates hide a line. Button actions and access checks remain unchanged.

The ticket opener's submitted message now appears in the same panel, directly
below the welcome sections and a divider. Its default identity line is plain
`username (user ID)`; the response uses medium bold heading text. Multiple intake
questions retain their question labels. The username is captured when the ticket
opens. Full intake responses are included in saved transcripts. The complete panel
is checked before ticket creation; responses that exceed Discord's message limits
must be shortened rather than creating a ticket with a missing introduction.


### Private ticket threads and delayed archival

New tickets are private threads in the published panel's text channel, named
`username-1`, `username-2`, and so on using each member's durable server-scoped
count. The panel channel is also the thread parent; the former ticket category
setting applies only to existing legacy channel tickets. Reposting a panel elsewhere
affects future tickets; existing threads retain their original parent.

KY BOT requires View Channel, Send Messages, Send Messages in Threads, Create Private
Threads, Manage Threads, Read Message History, Attach Files and Embed Links in the
panel channel. Staff and participants need View Channel and Send Messages in Threads.
Configured support, manager and approver members are invited explicitly; members
cannot invite others. Discord administrators and users with Manage Threads retain
Discord's native moderator visibility. Do not grant that permission just to make
someone ticket support staff.

Discord private threads do not offer per-member read-only overrides. Approval requests
remain staff-only until approved; approval adds the opener and attempts to DM the
thread link. Closing saves the transcript, removes member/guest access, keeps the
thread available to staff, and records a durable deadline exactly 24 hours after
closure. The maintenance worker archives and locks it on its first pass after that
deadline (normally within a minute); later messages do not extend the deadline.
The deadline survives bot restarts. Archived tickets remain recoverable, not deleted.
Reopening unarchives/unlocks the thread, restores approved participants and cancels
the old archive deadline. Transfers change teams and invitations while preserving
the thread's parent. Existing legacy text-channel tickets keep their original lifecycle.

Close/decline/delete confirmations display the opener's username with the standard
log title decoration and keep Cancel as a right-side button.
