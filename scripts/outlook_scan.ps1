# scripts/outlook_scan.ps1
param([string]$Allowlist = "", [int]$Days = 60)
$ErrorActionPreference = 'Stop'
$allow = @($Allowlist.ToLower().Split(';') | Where-Object { $_ })
$ol = New-Object -ComObject Outlook.Application
$ns = $ol.GetNamespace('MAPI')
$sent = $ns.GetDefaultFolder(5)
$since = (Get-Date).AddDays(-$Days)
$PR_SMTP = 'http://schemas.microsoft.com/mapi/proptag/0x39FE001F'
$PR_MSGID = 'http://schemas.microsoft.com/mapi/proptag/0x1035001F'

function Smtp-Of-Recipient($m) {
  try {
    $r = $m.Recipients.Item(1)
    if ($r.AddressEntry.AddressEntryUserType -eq 0 -or $r.AddressEntry.Type -eq 'EX') {
      $eu = $r.AddressEntry.GetExchangeUser(); if ($eu) { return $eu.PrimarySmtpAddress.ToLower() }
    }
    try { return ($r.PropertyAccessor.GetProperty($PR_SMTP)).ToLower() } catch {}
    return ("" + $r.Address).ToLower()
  } catch { return ("" + $m.To).ToLower() }
}
function Smtp-Of-Sender($m) {
  try {
    if ($m.SenderEmailType -eq 'EX' -and $m.Sender) {
      $eu = $m.Sender.GetExchangeUser(); if ($eu) { return $eu.PrimarySmtpAddress.ToLower() }
    }
  } catch {}
  return ("" + $m.SenderEmailAddress).ToLower()
}
function Domain-Of($a) { $s = "" + $a; if ($s -match '@') { return ($s.Split('@')[-1]) } return '' }
function In-Allow($smtp) {
  if ($allow.Count -eq 0) { return $true }
  $d = Domain-Of $smtp
  foreach ($a in $allow) { if ($smtp -eq $a -or $d -eq $a) { return $true } }
  return $false
}

$items = $sent.Items; $items.Sort('[SentOn]', $true)
$sentOut = New-Object System.Collections.ArrayList
$convByRecipient = @{}
foreach ($m in $items) {
  try {
    if ($m.Class -ne 43) { continue }
    if ($m.SentOn -lt $since) { break }
    $smtp = Smtp-Of-Recipient $m
    if (-not (In-Allow $smtp)) { continue }
    $mid = ""; try { $mid = $m.PropertyAccessor.GetProperty($PR_MSGID) } catch {}
    [void]$sentOut.Add([ordered]@{
      conversation_id = "" + $m.ConversationID
      recipient_smtp  = $smtp
      sent_on         = $m.SentOn.ToString('s')
      message_id      = $mid
      subject         = "" + $m.Subject
    })
    if (-not $convByRecipient.ContainsKey($smtp)) { $convByRecipient[$smtp] = @{} }
    $convByRecipient[$smtp][("" + $m.ConversationID)] = $m
  } catch {}
}

# reply detection across folders via Conversation tree
$replies = New-Object System.Collections.ArrayList
function Walk-Conv($conv, $item, $leadDom, [ref]$hit) {
  if ($hit.Value) { return }
  try { $kids = $conv.GetChildren($item) } catch { return }
  foreach ($k in $kids) {
    try {
      if ($k.Class -eq 43) {
        $d = Domain-Of (Smtp-Of-Sender $k)
        if ($d -and $d -eq $leadDom) {
          $fld = try { $k.Parent.Name } catch { '?' }
          $hit.Value = @{ folder = $fld; received = $k.ReceivedTime.ToString('s'); dom = $d }; return
        }
      }
    } catch {}
    Walk-Conv $conv $k $leadDom $hit
  }
}
foreach ($smtp in $convByRecipient.Keys) {
  $leadDom = Domain-Of $smtp
  $hit = [ref]$null
  foreach ($cid in $convByRecipient[$smtp].Keys) {
    $anchor = $convByRecipient[$smtp][$cid]
    try { $conv = $anchor.GetConversation(); if ($conv) { foreach ($r in $conv.GetRootItems()) { Walk-Conv $conv $r $leadDom $hit } } } catch {}
    if ($hit.Value) { break }
  }
  if ($hit.Value) {
    [void]$replies.Add([ordered]@{ recipient_smtp = $smtp; from_domain = $hit.Value.dom; folder = $hit.Value.folder; received = $hit.Value.received })
  }
}

[ordered]@{ sent = $sentOut; replies = $replies } | ConvertTo-Json -Depth 6
