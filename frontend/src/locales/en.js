// English locale (I18N-1: copy externalized so a second locale can be added).
export default {
  app: {
    title: 'Parlamonitor',
    tagline: 'Find out what happens in Parliament',
    taglineSub: 'Search speeches, see how MPs voted, and follow bills every step of the way.',
    skipToContent: 'Skip to content',
    loading: 'Loading…',
    error: 'Something went wrong while loading data.',
    retry: 'Try again',
    notFound: 'The page you are looking for was not found.',
    backHome: 'Back to home',
    source: 'Source',
    sourceNote: 'Data is sourced from parlament.hu; processing by Parlamonitor.',
    openData: 'Open data',
    terms: 'Terms of use',
    dataTerms: 'Terms of use for parlament.hu data',
    pager: {
      nav: 'Pagination',
      prev: 'Previous page',
      next: 'Next page',
      status: 'Page {page} of {total}',
    },
  },
  // `representatives` is the merged Felszólalók page: representatives, nationality
  // advocates and the other speakers in one list, picked apart by category chips
  // (REP-1). `advocates` / `speakers` name two of those chips.
  nav: { home: 'Home', menu: 'Main menu', submenu: 'submenu', search: 'Search', sessions: 'Sittings', representatives: 'Speakers', advocates: 'Nationality advocates', speakers: 'Other speakers', officials: 'Office holders', portfolios: 'Portfolios', factions: 'Factions', bills: 'Bills', documents: 'Other documents', questions: 'Questions', votes: 'Votes', analyses: 'Analyses', analysesIndex: 'Overview', cohesion: 'Faction analysis', interjections: 'Interjections', settlements: 'Settlements', settlementReps: 'Own constituency', about: 'About' },
  share: {
    label: 'Share', menu: 'Share options', native: 'Share…',
    facebook: 'Facebook', x: 'X', bluesky: 'Bluesky',
    copy: 'Copy link', copied: 'Copied to clipboard!',
  },
  embed: {
    label: 'Embed',
    title: 'Embed this figure',
    cycleNote: 'The embed code shows data for {cycle}.',
    copy: 'Copy code',
    copied: 'Copied!',
    preview: 'Preview ↗',
    openInteractive: 'Interactive version ↗',
    noData: 'No data to display.',
  },
  cycle: { label: 'Cycle', all: 'All cycles', count: '{n} cycles',
           multiHint: 'You can select several cycles.',
           scope: '{cycle} data', scopeAll: 'All-cycles data' },
  home: {
    searchPlaceholder: 'Search for a phrase in the proceedings…',
    searchButton: 'Search',
    statsLead: 'Browse data on the work of the Hungarian National Assembly since {year}!',
    statsLeadNoYear: 'Browse data on the work of the Hungarian National Assembly!',
    stats: { sessions: 'sittings', speeches: 'speeches', sentences: 'sentences', representatives: 'representatives' },
    exploreSearch: 'Full-text search',
    exploreSearchDesc: 'Search any phrase and jump straight to the moment it was spoken on video.',
    exploreReps: 'Representatives & statistics',
    exploreRepsDesc: 'Browse representatives, their speeches and activity.',
    exploreSessions: 'Browse sittings',
    exploreSessionsDesc: 'Read a full sitting transcript, segmented by agenda item.',
    examplesTitle: 'Example searches',
    examplesLead: 'Click a topic to open the full search; the chart shows how often the term came up over time.',
    examplesEmpty: 'No matches in this cycle.',
    examplesMore: 'View more topics',
  },
  search: {
    title: 'Search the proceedings', placeholder: 'Term or "exact phrase"…', button: 'Search',
    results: 'results', resultsCapped: 'more than {n} results', noResults: 'No results for these filters.',
    tooSlow: 'The search matched too much of the corpus and was stopped. Try a longer or more specific term, or narrow the cycle or date range.',
    hint: 'Tip: use quotes for an exact phrase, e.g. "tisztelt ház".',
    filters: 'Filters', clearFilters: 'Clear filters', period: 'Term', dateFrom: 'From', dateTo: 'To',
    speaker: 'Speaker', faction: 'Faction', agendaType: 'Agenda type', all: 'All', watch: 'Watch', on: '·',
    // Multi-value filter (MultiSelect): the trigger's label and the panel's hint.
    selectedCount: '{n} selected', multiHint: 'You can pick several values.',
    sort: 'Sort:', sortRelevance: 'Relevance', sortNewest: 'Newest first', sortOldest: 'Oldest first',
    trendCaption: 'Occurrences of “{q}” over time',
    trendHint: 'Click a bar to narrow the search to that period.',
    breakdown: 'Breakdown of matches',
    breakdownFactions: 'Matches for “{q}” by faction',
    breakdownSpeakers: 'Matches for “{q}” by representative',
    cycleScope: 'Search is limited to speeches from {cycle}.',
    cycleScopeAll: 'switch to all cycles',
  },
  viewer: {
    transcript: 'Transcript', noTranscript: 'No transcript text is available for this speech.',
    videoOnly: 'Video only.', play: 'Play from here', prevSpeech: 'Previous speech', nextSpeech: 'Next speech',
    playBtn: 'Play', pauseBtn: 'Pause', mute: 'Mute', unmute: 'Unmute',
    fullscreen: 'Fullscreen', seek: 'Seek within speech', volume: 'Volume',
    estimatedTiming: 'Estimated timing',
    estimatedTimingTip: 'Video timing is a positional estimate (by character position), so it is approximate.',
    viewOnParlament: 'View original on parlament.hu', license: 'License', agenda: 'Agenda item', speaker: 'Speaker',
    copyLink: 'Copy link', linkCopied: 'Link copied to clipboard', sittingDay: 'sitting', backToSession: 'Back to sitting',
    speechType: 'Speech type',
  },
  // Speech annotation: readability (LIX) and lexical diversity (MATTR). The chip
  // shows only where a speech sits against the House's median — the raw scores are
  // uninterpretable on their own (the classic Swedish LIX labels are calibrated for
  // a long-word threshold of 6 and mean nothing at the Hungarian threshold of 8,
  // and MATTR is a bare ratio), so the number, its corpus quintile and the counts
  // behind it live in the tooltip instead. `*VsShort` is the dense sitting-day list.
  metrics: {
    lixName: 'Readability (LIX)',
    lixVs: {
      easier: 'easier to read',
      typical: 'typical',
      harder: 'harder to read',
    },
    lixVsShort: { easier: 'easier', typical: 'typical', harder: 'harder' },
    vsTip: 'Compared with the median of every measured speech in the House — the '
      + 'middle fifth counts as typical.',
    lixTip: 'Measured from the share of long words and the sentence length.',
    lixScore: 'LIX {value} — {band} of the House\'s speeches',
    lixCounts: '{words} words · {sentences} sentences · {perSentence} words/sentence · '
      + '{longShare}% long words (over 8 letters)',
    lixBand: {
      'very-easy': 'the easiest fifth',
      easy: 'the second-easiest fifth',
      average: 'the middle fifth',
      hard: 'the second-hardest fifth',
      'very-hard': 'the hardest fifth',
    },
    mattrName: 'Lexical diversity (MATTR)',
    mattrVs: {
      less: 'less varied vocabulary',
      typical: 'typical',
      more: 'more varied vocabulary',
    },
    mattrVsShort: { less: 'less varied', typical: 'typical', more: 'more varied' },
    mattrTip: 'What share of the word stems are distinct within any {window}-word '
      + 'window. Measured on lemmas, so Hungarian inflection does not masquerade as '
      + 'a richer vocabulary.',
    mattrScore: 'MATTR {value} — {band} of the House\'s speeches',
    mattrCounts: '{types} distinct stems · {tokens} words',
    mattrBand: {
      'very-low': 'the least varied fifth',
      low: 'the second-least-varied fifth',
      average: 'the middle fifth',
      high: 'the second-most-varied fifth',
      'very-high': 'the most varied fifth',
    },
  },
  // CAP policy topics (TOPIC-1..7). The chip says only the topic; everything that
  // qualifies it — how much of the speech it speaks for, what it won against, how
  // much stayed uncertain, and which model decided at which threshold — lives in
  // the panel that opens on click.
  topics: {
    chipTitle: 'Topic: {topic} — click for details',
    panelLabel: 'Topic of this speech',
    share: '{pct} of the classified text is about this ({n} passages).',
    alsoAbout: 'Also touched on',
    coverage: '{pct} of the speech could be classified confidently; the model '
      + 'makes no claim about the rest.',
    otherShare: '{pct} of the classified text is not policy content (greetings, '
      + 'points of order, personal remarks).',
    method: 'Classified automatically by the ParlaCAP model, paragraph by '
      + 'paragraph, at a {threshold}% confidence threshold. Indicative only, not '
      + 'an official categorisation.',
    // The same model over an iromány, but the text is a submitted document
    // rather than a speech, so the wording differs. Only the lines that change
    // live here; the rest fall back to the shared keys above.
    bill: {
      panelLabel: 'Topic of this document',
      coverage: '{pct} of the document could be classified confidently; the '
        + 'model makes no claim about the rest.',
      otherShare: '{pct} of the classified text is not policy content (cover '
        + 'sheet, registry stamps, signatures, procedural formulae).',
      method: 'Classified automatically by the ParlaCAP model from the text of '
        + 'the submitted document, block by block, at a {threshold}% confidence '
        + 'threshold. Indicative only, not an official categorisation.',
    },
    names: {
      Macroeconomics: 'Macroeconomics',
      'Civil Rights': 'Civil rights',
      Health: 'Health',
      Agriculture: 'Agriculture',
      Labor: 'Labour and employment',
      Education: 'Education',
      Environment: 'Environment',
      Energy: 'Energy',
      Immigration: 'Immigration',
      Transportation: 'Transport',
      'Law and Crime': 'Law and crime',
      'Social Welfare': 'Social welfare',
      Housing: 'Housing and development',
      'Domestic Commerce': 'Banking and domestic commerce',
      Defense: 'Defence',
      Technology: 'Science and technology',
      'Foreign Trade': 'Foreign trade',
      'International Affairs': 'Foreign affairs',
      'Government Operations': 'Government operations',
      'Public Lands': 'Public lands and water',
      Culture: 'Culture',
      Other: 'Other, non-policy',
    },
  },
  clipExport: {
    button: 'Download video',
    segmentButton: 'Download this sentence as a video clip',
    title: 'Download a video clip',
    close: 'Close',
    segment: 'Segment',
    from: 'From',
    to: 'To',
    length: 'Length',
    subtitles: 'Subtitles',
    subNone: 'No subtitles',
    subNoneHint: 'video only',
    subSoft: 'Soft subtitles',
    subSoftHint: 'fast, video not re-encoded',
    subBurn: 'Burned-in subtitles',
    subBurnHint: 'painted into the picture; for social video',
    wholeSpeech: 'Whole speech',
    keepOpen: 'Keep this window open until it finishes.',
    watermark: 'Parlamonitor watermark',
    watermarkHint: 'logo top-right, date top-left (re-encodes the video)',
    noTextNote: 'This speech has no transcript, so it can only be exported without subtitles.',
    reencodeWarn: 'This option (burned-in subtitles, watermark or portrait format) re-encodes the video, so the download can be markedly slower.',
    mobileWarn: 'You appear to be on a mobile device. In-browser video processing can be much slower here and uses a lot of memory — use a desktop computer if you can.',
    format: 'Format',
    orient_landscape: 'Landscape',
    orient_portrait: 'Portrait',
    orientHint_landscape: 'original',
    orientHint_portrait: 'TikTok / Reels',
    quality: 'Quality',
    quality_low: 'Low',
    quality_medium: 'Medium',
    quality_high: 'High',
    start: 'Start export',
    cancel: 'Cancel',
    cancelled: 'Export cancelled.',
    phaseLoading: 'Loading video engine…',
    phaseFetching: 'Downloading video…',
    phaseEncoding: 'Assembling file…',
    phaseEncodingBurn: 'Re-encoding video with subtitles…',
    ready: 'Your clip is ready.',
    download: 'Save MP4',
    saved: 'Saving started',
    savedNote: 'The download has started — look for the file among your browser’s downloads.',
    another: 'New clip',
    provenance: 'The file is produced in your browser from the public parlament.hu recording and the official transcript. Source: Hungarian National Assembly.',
    unsupported: 'Your browser does not support in-browser video export.',
    failed: 'Could not produce the clip. Please try again.',
  },
  entity: {
    uncertain: 'Uncertain match',
    profile: 'Profile',
    kmonitor: 'K-Monitor database',
    wikipedia: 'Wikipedia',
    timeMarker: 'Time in the proceedings',
  },
  sessions: {
    title: 'Sittings', date: 'Date', sitting: 'Sitting', speeches: 'speeches', agendaItems: 'agenda items',
    open: 'Open', agenda: 'Agenda', duration: 'Duration',
    dayNav: 'Go to another sitting day', prevDay: 'Previous sitting', nextDay: 'Next sitting',
    wordcloud: 'What was discussed on this day?',
    wordcloudCaption: 'Words most characteristic of this day: frequent here but rare on the cycle\'s other sitting days (TF·IDF). Words are shown as their lemma (inflected forms merged) and recognized names (people, places, organisations) appear in italics. Chairing and common filler words excluded. Click a word to search that day.',
    wordcloudCount: 'occurrences',
    wordcloudEntity: 'name',
    topicsPreview: 'Topics of the day',
    topSpeakers: 'Who spoke the most on this day?',
    topSpeakersCaption: 'Representatives by total speaking time on this sitting day (chairing and procedural speeches excluded). Click a name to open the representative\'s profile.',
    newWords: 'Which words were said for the first time?',
    metricsLabel: 'Speech metrics',
    metricsShow: 'Speech metrics',
    metricsHide: 'Hide speech metrics',
    metricsCaption: 'Two comparisons per speech: how hard it is to read (LIX — the '
      + 'share of long words and the sentence length) and how varied its vocabulary '
      + 'is (MATTR — how distinct the word stems are). Each says only where the '
      + 'speech sits against the median of every speech in the House, since neither '
      + 'score means anything on an absolute scale; hover a chip for the number '
      + 'itself. Only substantive speeches of sufficient length are measured — '
      + 'chairing and short remarks carry no score. Hidden by default: switching '
      + 'it on here also annotates the individual speech pages, and is remembered '
      + 'on this device.',
    newWordsCaption: 'These lemmas were spoken in parliament for the first time on this sitting day — never before, previous cycles included (based on the transcripts available). Names and capitalized (proper-noun) words are excluded. The number is how often it was said that day. Click a word to search that day.',
    showTranscript: 'Show transcript',
    hideTranscript: 'Hide transcript',
    openViewer: 'Open video & transcript',
    // The day has a recording but is not cut into per-speech clips yet, so the
    // viewer opens the whole-day video rather than this speech — promise that,
    // not what `openViewer` promises.
    openDayVideo: 'Open the day\'s full recording',
    transcriptLoading: 'Loading transcript…',
    transcriptLoadError: 'Could not load the transcript.',
    upcoming: 'Upcoming',
    upcomingNote: 'This sitting is already on the Assembly\'s schedule — the recording and transcript will be available soon.',
    notProcessed: 'This sitting has not been processed yet.',
    notReady: 'Being processed',
    notReadyNote: 'The National Assembly has not fully published this sitting day yet: per-speech timings, video and transcript are not available. They will appear here automatically once released.',
    // Same state, but the whole-day recording is already published — the ▶ on each
    // speech opens it (there are no per-speech clips and no transcript yet).
    notReadyVideoNote: 'The National Assembly has not fully published this sitting day yet: per-speech timings and the transcript are not available. The day\'s full recording is already watchable, though — open it with the ▶ icon. The rest will appear here automatically once released.',
    // Browsable, but the Assembly publishes a day in instalments (the transcript
    // lands days after the recording, and per-speech video windows arrive in
    // batches), so mark a day that is not complete yet.
    partial: 'Partly processed',
    partialNote: 'The National Assembly has not published the transcript or the video clip of every speech of this sitting day yet. Whatever is missing is picked up automatically as it appears.',
    // The Assembly has not linked this day's speeches to agenda items yet, so the
    // day is shown as a single chronological list.
    unlistedAgenda: 'Speeches of the sitting day',
  },
  reps: {
    title: 'Speakers',
    // The switch at the top of the search card: the same question asked by name
    // (the list, REP-1) or by place ("Find your MP", REP-10). The place branch is
    // labelled with lookup.title, so the question is worded in one place only.
    searchMode: 'Search by',
    modeName: 'By name',
    // Category chips under the search box; representatives by default (REP-1).
    category: 'Category',
    role: {
      mp: 'Representatives',
      advocate: 'Nationality advocates',
      other: 'Other speakers',
      all: 'All',
    },
    allNote: 'Everyone with a part in the National Assembly, in one list: the '
      + 'representatives, the nationality advocates, and those who speak without '
      + 'a mandate — ministers, state secretaries, the President of the Republic '
      + 'and invited guests.',
    searchPlaceholder: 'Search representatives by name…', faction: 'Faction',
    constituency: 'Constituency', speeches: 'speeches', speakingTime: 'speaking time', sortName: 'By name',
    sortSpeeches: 'By speeches', sortSpeakingTime: 'By speaking time', noResults: 'No matching representatives.', profile: 'Profile',
    filterByFaction: 'Filter representatives of {faction}',
    // Terminated mandates (REP-14). A cycle lists everyone who held a mandate in it;
    // "active" is read against that cycle, not against today.
    mandateFilter: 'Mandate',
    mandateAll: 'All',
    mandateActive: 'Active',
    mandateTerminated: 'Terminated',
    mandateFilterNote: '"Active" is read against the selected cycle: in the running '
      + 'cycle it means still sitting, in a closed one that the mandate lasted to the '
      + 'end of the term.',
    mandateEnded: 'mandate terminated',
    // Nationality advocates (szószólók): they sit and speak without a mandate (REP-9).
    mandate: 'Mandate',
    advocateFor: '{nationality} nationality advocate',
    advocatesUnit: 'advocates',
    advocateNote: 'Each of Hungary\'s 13 recognised nationalities elects an advocate '
      + '(szószóló). Advocates take part in the work of the Assembly — they speak and '
      + 'submit documents — but they are not representatives: they have no faction, no '
      + 'constituency and no vote.',
    searchAdvocatePlaceholder: 'Search advocates by name…',
    noAdvocateResults: 'No matching advocates.',
    // Other speakers: they spoke in the House holding no mandate (REP-12).
    othersUnit: 'speakers',
    otherNote: 'Not only representatives speak in the National Assembly: so do '
      + 'ministers and state secretaries — who are often not MPs — the President '
      + 'of the Republic, the heads of independent bodies and invited guests. '
      + 'They are listed here: they have no faction, no constituency and no vote, '
      + 'and it is their office that identifies them.',
    searchOtherPlaceholder: 'Search speakers by name…',
    noOtherResults: 'No matching speakers.',
  },
  // Office holders (REP-11) — the Assembly's official registry back to 1990, one
  // row per term of office.
  officials: {
    title: 'Office holders',
    intro: 'Who held which government or parliamentary office, and from when to '
      + 'when — according to the National Assembly\'s official registry, from '
      + '1990 to today. One row is one term, so the same person can appear under '
      + 'several offices; the list also includes people who never spoke in the House.',
    unit: 'terms of office',
    searchPlaceholder: 'Search by name or office…',
    category: 'Type of office',
    status: 'Status',
    statusCurrent: 'Still in office',
    statusPast: 'Former',
    // A cycle shows the terms *held during* it, not only those that began in it —
    // so the label says which, rather than the generic "{cycle} data".
    scopeHeld: 'held during {cycle}',
    scopeStarted: 'began in {cycle}',
    startedHint: '{count} of them began in this cycle',
    startedShowAll: 'show all {count}',
    started: 'Start',
    startedAll: 'All',
    startedInCycle: 'Began in this cycle',
    inOffice: 'in office',
    isMp: 'representative',
    sortStart: 'By start date',
    sortOffice: 'By office',
    noResults: 'No matching terms of office.',
    // The registry's own categories (the portal's own filters).
    categories: {
      pm: 'Prime Minister',
      minister: 'Minister',
      'state-secretary': 'State secretary',
      parliamentary: 'Parliamentary office holder',
      senior: 'Other senior office',
      other: 'Other office',
      uncategorised: 'Uncategorised',
    },
  },
  // "Find your MP" — settlement → single-member constituency → MP (REP-10).
  // Portfolios (§6C): the government side of the record.
  portfolios: {
    title: 'Portfolios',
    intro: 'Which ministry was asked what, which portfolio laid which documents '
      + 'before the House, and what its ministers said in plenary. A portfolio is '
      + 'the institution behind the offices: the ministerial, state-secretary and '
      + 'ministry forms the record uses — "belügyminiszter", "Belügyminisztérium '
      + 'államtitkára", "kormány (belügyminiszter)" — all belong to one portfolio.',
    unit: 'portfolios',
    searchPlaceholder: 'Search by portfolio name…',
    scope: 'in {cycle}',
    noResults: 'No portfolio matches these filters.',
    backToList: 'Portfolios',
    kinds: {
      ministry: 'Ministries',
      pm: 'Prime Minister',
      'no-portfolio': 'Ministers without portfolio',
      other: 'Other government offices',
      body: 'Independent state bodies',
    },
    bodyNote: 'Not part of the government, but they answer to the House and '
      + 'reply to members\' questions in the same way.',
    answered: 'questions answered',
    submitted: 'documents submitted',
    speeches: 'speeches',
    answeredShort: 'questions',
    submittedShort: 'documents',
    speechesShort: 'speeches',
    medianDays: 'days — median time to answer a written question',
    holders: 'Who held it',
    holdersMore: '{count} more office holders',
    holdersFewer: 'Show fewer',
    // Only a few of a tárca's leads fit on a list row; the profile has the rest.
    leadsMore: '+{count} more',
    trendTitle: 'Questions answered per year',
    trendCaption: 'Questions answered by this portfolio, per year.',
    emptyPanel: 'Nothing of this kind in the selected cycle.',
    noAgenda: 'No agenda item',
    aliases: 'Labels collated',
    answeredNote: 'Only answered questions are listed for now: the documents\' '
      + 'addressee has not been ingested yet, so questions left unanswered do not '
      + 'appear here and no answer rate is shown.',
    speechCoverage: 'The office a speaker spoke in is only recorded for the cycles '
      + 'we have re-ingested since — elsewhere a zero means we have no such data '
      + 'for that cycle, not that the portfolio never spoke.',
    methodology: 'A portfolio is identified from the source\'s own labels: the '
      + 'answering office on a document event, the government submitter '
      + '("kormány (…)"), the office recorded on a speech, and the office-holder '
      + 'registry\'s terms. A hand-reviewed, published table maps those labels to '
      + 'portfolios; a label the table does not cover stands alone under its own '
      + 'name rather than being folded into a neighbour. Renames are not merged: '
      + 'the Nemzeti Erőforrás Minisztérium and the Emberi Erőforrások '
      + 'Minisztériuma are listed separately, because the succession is not '
      + 'something the data states. Personal commissions (miniszterelnöki biztos, '
      + 'kormánymegbízott) belong to no portfolio and are left out.',
  },
  lookup: {
    title: 'Find your MP',
    intro: 'Enter your settlement and we will show which single-member constituency '
      + 'it belongs to, and who represents it. Most settlements have a single '
      + 'constituency; larger cities and Budapest districts are divided between '
      + 'several — for those, pick yours on the map.',
    searchLabel: 'Settlement',
    searchPlaceholder: 'e.g. Debrecen, Pécs, Budapest 09. kerület…',
    searchHint: 'Accents don\'t matter, and Budapest districts are also found as '
      + '"V. kerület" or "5. kerület".',
    clear: 'Clear',
    noSettlement: 'No such settlement. Try a shorter part of the name.',
    splitBadge: '{count} constituencies',
    singleAnswer: 'All of {name} lies in {constituency}.',
    splitAnswer: '{name} is divided between {count} single-member constituencies.',
    splitHelp: 'Pick the part of the settlement you live in on the map — or choose '
      + 'directly from the list.',
    pickerLabel: 'The settlement\'s constituencies',
    pickPrompt: 'Choose a constituency to see its representative.',
    mapLabel: 'Map of the settlement\'s constituencies',
    mapAriaFor: 'Map of the constituencies of {name} — click the part where you live',
    mapFailed: 'The map could not be loaded. You can also pick the constituency from the list below.',
    noGeometry: 'Constituency boundaries are unavailable right now; please choose from the list.',
    noMp: 'We found no representative for this constituency in our database.',
    noConstituency: 'We found no constituency for this settlement.',
    writeEmail: 'Write an email',
    copyEmailOf: 'Copy email address: {email}',
    emailCopied: 'Copied!',
    emailCopiedOf: '{email} copied to the clipboard.',
    noEmail: 'No public email address for this representative.',
    scope: 'This answer applies to the {cycle} term: constituency boundaries may be '
      + 'redrawn before each election.',
    listNote: 'Besides the single-member constituency MP, representatives elected '
      + 'from the national list are also members of the Assembly, but they are not '
      + 'tied to a constituency.',
    listNoteLink: 'All representatives',
    unavailable: 'Constituency data is currently unavailable (the National Election '
      + 'Office source is not responding). Please try again later.',
    methodology: 'Methodology & data source',
    methodologyText: 'The settlement-to-constituency mapping and the constituency '
      + 'boundaries come from the National Election Office; the representatives and '
      + 'their mandates come from parlament.hu. Single-member constituency boundaries '
      + 'may be redrawn for each election, so this answer applies to the term that '
      + 'election produced — not to the term selected in the header. The '
      + 'representative shown won their mandate in that constituency.',
    sourceLine: 'Constituency data source: {name} —',
  },
  profile: {
    speeches: 'Speeches',
    questions: 'Submitted questions & interpellations',
    bills: 'Submitted bills & resolution proposals',
    otherDocuments: 'Other submitted documents',
    statistics: 'Statistics', biography: 'Details', office: 'Office', mandate: 'Mandate',
    // Terminated mandate (REP-14): a seat given up before the term ended, with
    // parlament.hu's own reason and the MPs on either side of the handover. The
    // reason itself is upstream's Hungarian wording, shown verbatim.
    mandateEndedNote: 'The mandate ended before the term did.',
    mandateTermNote: 'How long the mandate was held in this cycle.',
    predecessor: 'Predecessor in the seat',
    successor: 'Successor in the seat',
    officeHistory: 'Offices held',
    officeTermApprox: 'at least {range}',
    officeTermSince: 'since at least {date}',
    officeTermNote: 'The period this office was held.',
    officeTermSpeechesNote: 'The exact term is not reported; the dates come from their speeches — when they spoke holding this office.',
    constituency: 'Constituency',
    education: 'Highest education', email: 'Email', website: 'Website', committees: 'Committee memberships',
    factionHistory: 'Faction history', wikipedia: 'Wikipedia', kmonitor: 'K-Monitor', cv: 'CV',
    assetDeclarations: 'Asset declarations',
    assetDeclarationsNote: 'The representative\'s asset declarations as published on parlament.hu; every entry links to the original PDF. The date is when the declared assets were held — the point in time the declaration describes. The list covers every cycle, regardless of the selected one.',
    assetDeclarationDate: 'assets as of {date}',
    assetDeclarationMissing: 'No published document',
    assetDeclarationDeadline: 'filing deadline: {date}',
    // Astrological signs (REP-16). Openly trivia: derived from the Wikidata birth
    // date, of no analytical value. Keys are language-neutral; labels live here.
    zodiac: 'Star sign',
    chineseZodiac: 'Chinese zodiac',
    // On the profile the signs sit behind a spoiler — the page opens showing only
    // the ⛎ glyph. This label is the button's only readable content, so it says
    // what pressing it does (A11Y-1).
    zodiacReveal: 'Show star signs',
    zodiacHide: 'Hide star signs',
    zodiacNote: 'Curiosity only, not analysis: both signs follow from the birth date on the person\'s Wikidata entry (the sun sign from the date, the animal year aligned to the Chinese lunar new year). There is no connection whatsoever between them and the person\'s work, and Parlamonitor builds no statistics on them. Where no day-precision birth date exists, no sign is shown.',
    zodiacSign: {
      aries: 'Aries', taurus: 'Taurus', gemini: 'Gemini', cancer: 'Cancer',
      leo: 'Leo', virgo: 'Virgo', libra: 'Libra', scorpio: 'Scorpio',
      sagittarius: 'Sagittarius', capricorn: 'Capricorn', aquarius: 'Aquarius',
      pisces: 'Pisces',
    },
    chineseSign: {
      rat: 'Rat', ox: 'Ox', tiger: 'Tiger', rabbit: 'Rabbit',
      dragon: 'Dragon', snake: 'Snake', horse: 'Horse', goat: 'Goat',
      monkey: 'Monkey', rooster: 'Rooster', dog: 'Dog', pig: 'Pig',
    },
    totalSpeeches: 'Number of speeches',
    totalSpeakingTime: 'Total speaking time', billsSubmitted: 'Bills submitted',
    votesAbsent: 'Occasions with no vote cast',
    voteBreakdown: 'Voting participation',
    vbUnit: 'votes',
    vbVoted: 'Voted',
    vbNovote: 'Present, did not vote',
    vbAbsent: 'Excused absence',
    vbNotPresent: 'Not present',
    vbNotMp: 'Not an MP at the time',
    vbNotMpNote: 'These votes took place before (or after) their mandate, so they are not counted in the participation total.',
    billsUnavailable: 'Bills-submitted data will be available once the Bills module ships.',
    activity: 'Activity',
    activityHelp: 'Daily activity: the number of speeches given and documents submitted on that day. Darker means more activity. We got the idea from GitHub\'s similar chart.',
    activityAriaLabel: 'Activity calendar: {days} active days',
    activityDocs: 'documents',
    activityWindow: 'Shows at most the last 200 days.',
    methodology: 'Methodology', scope: 'Data scope',
    sessionsCovered: 'sittings covered', noSpeeches: 'No speeches on record.', viewSpeech: 'View',
    speechesDayCount: 'speeches', speechesLoadError: 'Could not load speeches.',
    showMore: 'Show more', showLess: 'Show less',
    showingFirst: 'Showing the first {n} items.',
    present: 'present',
    votes: 'Votes', votesDayCount: 'votes', votesLoadError: 'Could not load votes.',
    votesNote: "The representative's recorded roll-call votes in processed divisions.",
    noVotes: 'No recorded roll-call votes.', viewVote: 'View vote', allVotes: 'View all votes',
  },
  // Comparison (REP-15): speakers side by side, spec-sheet style.
  compare: {
    title: 'Compare speakers',
    intro: 'Up to {max} speakers side by side, the same figure on every row.',
    tableCaption: 'Comparison table: one figure per row, one speaker per column.',
    compareAction: 'Compare', compareWith: 'Compare with others',
    emptyLead: 'Pick speakers and put them side by side.',
    emptyHint: 'You can start a comparison from any speaker\'s profile.',
    addPerson: 'Add a speaker', close: 'Close',
    pickerHint: 'Start typing a name, or pick from the cycle\'s most active speakers.',
    alreadyIn: 'already there',
    remove: 'Remove {name} from the comparison',
    needTwo: 'Add one more speaker so there is something to compare against.',
    missing: 'No speaker found with this id: {ids}. That column was left out.',
    dropped: 'At most {n} speakers can be compared at once; the rest were left out.',
    diffOnly: 'Differences only', diffOnlyCount: '{n} identical rows',
    // Neutral by design: the page reports who spoke more, not who is better.
    largest: 'the largest value in this row',
    // Two different empty cells: the notion does not apply to this person, vs.
    // it applies and we simply don't know it. Conflating them is a factual error.
    na: 'not applicable',
    unknown: 'no data',
    sectionWho: 'Who they are', sectionSpeech: 'Speeches',
    sectionDocs: 'Submitted motions', sectionVotes: 'Voting', sectionOther: 'Career',
    avgSpeech: 'Average speech length',
    avgSpeechNote: 'Total speaking time divided by the number of speeches: whether the total is made of many short interjections or a few long addresses.',
    sentences: 'Sentences',
    speakingDays: 'Sitting days with a speech',
    speakingDaysNote: 'The number of processed sitting days on which the speaker took the floor at least once.',
    ownMotionsNote: 'parlament.hu\'s own statistic on the MP\'s own motions. The rows below count the motions Parlamonitor has processed, by type, so the two numbers need not match.',
    rollCalls: 'Roll-call votes',
    rollCallsNote: 'The roll-call votes the MP has a recorded vote in. Quorum-establishment votes are excluded, as they are from parlament.hu\'s own statistics.',
    careerNote: 'This figure covers the whole career, not only the selected cycle.',
    profileRow: 'Profile', openProfile: 'Open profile',
  },
  factions: {
    title: 'Factions', subtitle: 'Aggregate statistics on the speeches of the MPs in each faction.',
    members: 'members', membersLink: 'members', speeches: 'speeches',
    speakingTime: 'speaking time', avgPerMp: 'avg / MP', methodology: 'Methodology',
  },
  bills: {
    title: 'Bills', subtitle: 'Bills submitted to the National Assembly.',
    figyusz: 'Use Figyusz! notifications to follow parliamentary documents!',
    searchPlaceholder: 'Search the title or bill number…', period: 'Cycle', status: 'Status',
    // The machine-read label (TOPIC-8); the filter returns exactly the rows
    // whose chip shows it.
    topic: 'Topic',
    sortNumber: 'By bill number', sortDate: 'By submission', count: 'bills',
    bySponsor: 'Submitter', viewProfile: 'View profile', clearSponsor: 'Clear filter',
    noResults: 'No matching bills.', submitters: 'Submitters',
    submittedDate: 'Submission date',
    timeline: "The bill's progress",
    timelineNote: 'Legislative stages in order; completed steps are highlighted, upcoming ones dimmed.',
    stageDone: 'completed', stageCurrent: 'current status', stagePending: 'not yet reached',
    source: 'Source', openText: 'Bill text (PDF)',
    showDocument: 'Show document', hideDocument: 'Hide document', openInNewTab: 'Open in new tab',
    noText: 'No downloadable text is available for this bill.',
    viewOnParlament: 'View on parlament.hu',
    votes: 'Votes',
    viewRollCall: 'Roll-call result',
    yes: 'Yes', no: 'No', abstain: 'Abstain', voteTag: 'vote',
    // The derived figures the vote cards in the Votes list carry — same numbers,
    // same wording (cf. votes.attendance / votes.crossVoting).
    attendance: 'Attendance',
    attendanceTitle: 'Attendance: {present} votes cast out of {seats} seats. ' +
      '“Present, did not vote” and “excused absent” do not count as attendance.',
    crossVoting: 'against faction line',
    crossVotingTitle: '{n} representatives voted against their own faction’s position — ' +
      '{pct} of the votes cast. The Assembly’s own “frakcióval szemben” figure.',
    events: 'Bill events',
    speechNumber: 'Speech no.',
    viewSpeech: 'View this speech',
    videoAnswer: 'Video answer',
    openInViewer: 'Open the full speech with transcript',
    debates: 'Debate speeches',
    debatesNote: 'The plenary speeches between the opening and closing of the debate, in order. Click a speech to watch it on video.',
    debateSpeechCount: 'speeches',
    noTranscript: 'No transcript',
    showAllSpeeches: 'Show all {n} speeches',
    showAllMotions: 'Show all {n} motions',
    showLess: 'Show less',
    committeeEvents: 'Committee events',
    committees: 'Negotiating committees',
    deadlines: 'Deadlines',
    motions: 'Non-self-standing motions',
    motionSummary: 'Non-self-standing motions — summary by type',
    documents: 'Justifications & background',
    date: 'Date', event: 'Event', committee: 'Committee',
    amendment: 'Amendment', report: 'Report',
    name: 'Name', deadline: 'Deadline', reference: 'Reference',
    type: 'Type', valid: 'Valid', withdrawn: 'Withdrawn', total: 'Total',
    meta: {
      subtype: 'Type', character: 'Character', negotiationMode: 'Negotiation mode',
      currentEvent: 'Current event', promulgationNumber: 'Promulgation no.',
      mkNumber: 'Official Gazette no.', promulgationDate: 'Promulgation date',
      lastModifier: 'Last amending bill no.', remark: 'Remark',
      kozlony: 'Official Gazette',
    },
    kozlonyLink: 'View in the Magyar Közlöny',
    docKind: { justification: 'Justification', background: 'Background' },
  },
  documents: {
    title: 'Other documents',
    subtitle: 'Other documents (irományok) submitted to the National Assembly (resolution proposals, interpellations, questions, reports…) — bills have their own page.',
    // Scoped to one representative the list covers every document type, bills
    // included — matching how the profile's submitted-documents stat counts.
    sponsorTitle: 'Submitted documents',
    sponsorSubtitle: 'Documents submitted by one representative — every type, bills included.',
    bySponsor: 'Submitter',
    viewProfile: 'View profile',
    clearSponsor: 'Clear filter',
    searchPlaceholder: 'Search the title or document number…',
    type: 'Type', period: 'Cycle', status: 'Status',
    topic: 'Topic',
    verdict: 'Answer accepted',
    verdictAccepted: 'the MP accepted the answer',
    verdictRejected: 'the MP rejected the answer',
    verdictHint: 'Applies to interpellations only.',
    sortNumber: 'By document number', sortDate: 'By submission', count: 'documents',
    noResults: 'No matching documents.', submitters: 'Submitters',
    answeredBy: 'Answered by',
    mainType: {
      T: 'Bills', H: 'Resolution proposals', I: 'Interpellations',
      K: 'Questions', A: 'Immediate questions', B: 'Reports',
      S: 'Personnel decisions', Y: 'Briefings',
    },
  },
  questions: {
    title: 'Questions',
    subtitle: 'Who asks and who answers? The flow of MPs’ questions, interpellations and immediate questions from the asker’s faction to the answering ministry — optionally broken down by question type.',
    count: 'questions',
    noResults: 'No question data for this cycle.',
    chartCaption: 'Flow of questions from the asker’s faction to the answerer.',
    clickHint: 'Click a flow to list the questions behind it, or a node (type / faction / answerer) for all of its questions.',
    hideType: 'Hide question type',
    showType: 'Show question type',
    ungroupOther: 'Ungroup “Other ministry”',
    groupOther: 'Group “Other ministry”',
    openPortfolio: 'Open portfolio page',
    close: 'Close',
    askerHeading: 'Asker (faction)',
    typeHeading: 'Question type',
    answererHeading: 'Answerer',
    methodology: 'The asker is the faction of the MP who submitted the question; the answerer is the responding portfolio, whether answered orally or in writing. The source names the answerer by office ("Belügyminisztérium államtitkára", "belügyminiszter"); the Portfolios page\'s table collates those into one ministry, so a portfolio is a single node, and a label the table does not cover keeps its own name. Use “Show question type” to prepend a column that splits the flow by question type (interpellation, question, immediate question or written). Only the busiest ministries appear separately — the rest are pooled into an “Other ministry” node; “Ungroup «Other ministry»” opens that pool so every responder gets its own row. The cycle is set by the header’s cycle selector.',
    type: {
      I: 'Interpellation',
      K: 'Question',
      A: 'Immediate question',
      W: 'Written question',
    },
    node: {
      oral: 'Answered orally',
      other: 'Other ministry',
      unnamed: 'Unnamed ministry',
      unanswered: 'Unanswered',
      nofaction: 'Independent / other',
    },
  },
  // Interjections (§6E) — who shouts over whose speech.
  interjections: {
    title: 'Interjections',
    subtitle: 'Who shouts over whose speech? The heckling the shorthand writers recorded verbatim, drawn as a directed network: each arrow runs from the member who shouted to the one being interrupted, and its thickness is how many times.',
    count: '{n} interjections between {people} members',
    listCount: '{n} interjections',
    noResults: 'No interjections on record for this term.',
    chartCaption: 'Who interjects over whose speech: each arrow runs from the heckler to the member holding the floor.',
    clickHint: 'Click an arrow to read the interjections behind it, or a member to see everything they shouted.',
    topLabel: 'Top {n} members',
    rankLabel: 'What the chart ranks by',
    rank: {
      total: 'Total',
      made: 'Interjected',
      received: 'Was interjected at',
    },
    shownShareTotal: 'The chart shows the {n} members most involved in this cross-talk: the interjections between them are {share}% of all of them.',
    shownShareMade: 'The chart shows the {n} members who interject the most: the interjections between them are {share}% of all of them.',
    shownShareReceived: 'The chart shows the {n} members interrupted the most: the interjections between them are {share}% of all of them.',
    unattributed: 'A further {n} are written in a way that could not be pinned to a single member, and are left off the chart.',
    bothWays: '— everything they shouted, and everything shouted at them',
    close: 'Close',
    during: 'interrupted:',
    watch: 'Watch',
    zoomIn: 'Zoom in',
    zoomOut: 'Zoom out',
    resetView: 'Reset the view',
    made: 'Interjections made',
    received: 'Interjections received',
    dragHint: 'The chart can be zoomed and panned, and any member dragged out of the tangle.',
    methodology: 'The shorthand record puts the louder heckling in parentheses, verbatim, inside the speech it interrupted: "(Balla György: Úgy van!)". Parlamonitor lifts those out and joins the two members: the heckler by the name in the parenthesis, the other end by whoever held the floor. A name counts only when it fits exactly one member — where two members of the same House shared a name (two Tóth Istváns, say), the interjection is left unattributed rather than guessed at. The transcript reader applies the same rule when it links a heckler\'s name to their profile, so the chart and the transcript agree.',
    methodologyExclusions: 'Interjections shouted over a chairing speech are not counted: a voting block is one hours-long speech by the presiding officer in the record, so a whole afternoon of heckling would land inside it and the deputy speakers would be the most-interrupted members of the House by a wide margin — the same rule that keeps chairing out of every other representative statistic. Nor are the ones where the record notes the event but not the words ("Gulyás Gergely közbeszól."), or the words but not the heckler ("Közbeszólások a Fidesz padsoraiból: Nem!").',
    coverage: 'In the selected term {extracted} verbatim interjections were found, {attributed} of them attributable to a single member; {procedural} were shouted over a chairing speech.',
  },
  analyses: {
    lead: 'Computed views of the House at work: not a single speech or vote, but '
      + 'what thousands of them add up to.',
    note: 'Every analysis here is Parlamonitor\'s own calculation from the National '
      + 'Assembly\'s public data. Each page carries its methodology, so you can see '
      + 'exactly what the number measures — and what it does not.',
    needsCycle: 'This analysis only makes sense within a single term: pick one term to open it.',
    empty: 'No analyses are available in this deployment.',
    cards: {
      cohesion: {
        source: 'From the votes',
        title: 'Faction analysis',
        desc: 'How closely factions vote together, and how well each holds its own '
          + 'line — agreement matrix, cohesion bars and bloc map.',
      },
      questions: {
        source: 'From the documents',
        title: 'Questions and interpellations',
        desc: 'Who asks and who answers: the path of a question from the asker\'s '
          + 'faction to the ministry that replies.',
      },
      interjections: {
        source: 'From the proceedings',
        title: 'Interjections',
        desc: 'Who shouts over whose speech — the heckling the record kept '
          + 'verbatim, drawn as a directed network, with the words behind '
          + 'every arrow.',
      },
      settlements: {
        source: 'From the proceedings',
        title: 'Settlements',
        desc: 'Which Hungarian settlements get named in plenary, how often and by '
          + 'whom — and which have never come up at all.',
      },
    },
  },
  votes: {
    title: 'Votes',
    subtitle: 'Roll-call and list divisions of the National Assembly.',
    searchPlaceholder: 'Search the subject or bill number…',
    period: 'Cycle',
    result: 'Result',
    count: 'votes',
    noResults: 'No matching votes.',
    all: 'All',
    date: 'Time',
    subject: 'Subject of the vote',
    votingMode: 'Voting mode',
    dateFrom: 'From',
    dateTo: 'To',
    viewOnParlament: 'View on parlament.hu',
    decidedBills: 'Bills decided',
    yes: 'Yes', no: 'No', abstain: 'Abstain',
    absent: 'Excused absent', novote: 'Did not vote', other: 'Other',
    total: 'Total', totalVotes: 'Votes cast',
    result_: 'Result',
    rollCall: 'Roll call',
    rollCallNote: "Each representative's individual vote. Click a name to open their profile.",
    noRollCall: 'No per-representative breakdown is available for this vote (e.g. a list or show-of-hands vote).',
    byFaction: 'By faction',
    againstFaction: 'against faction line',
    backToList: 'Back to votes',
    viewBill: 'View bill',
    sortDate: 'By time',
    sort: 'Sort',
    sortNewest: 'Newest first',
    sortOldest: 'Oldest first',
    sortAttendanceDesc: 'Highest attendance first',
    sortAttendanceAsc: 'Lowest attendance first',
    sortCrossDesc: 'Most cross-voting first',
    sortCrossAsc: 'Least cross-voting first',
    crossVoting: 'against faction line',
    crossVotingTitle: '{n} representatives voted against their own faction’s position — ' +
      '{pct} of the votes cast. The Assembly’s own “frakcióval szemben” figure.',
    crossVotingNote: 'The “against faction line” column is the Assembly’s own count of how ' +
      'many members voted against their faction’s position. It does not say which members — ' +
      'so none are marked as such in the roll call.',
    attendance: 'Attendance',
    attendanceTitle: 'Attendance: {present} votes cast out of {seats} seats. ' +
      '“Present, did not vote” and “excused absent” do not count as attendance.',
    clearFilters: 'Clear filters',
    remark: 'Remark',
    accepted: 'Elfogadva',
    cohesion: {
      title: 'How factions vote together — and apart',
      subtitle: 'How closely — or how differently — the factions vote across the cycle’s roll-call divisions.',
      basis: 'based on {n} roll-call votes',
      empty: 'Not enough roll-call votes in this filter for a faction analysis.',
      tab_matrix: 'Agreement matrix',
      tab_bars: 'Bars',
      tab_map: 'Faction map',
      matrixCaption: 'How often factions vote the same way — the diagonal is internal cohesion.',
      low: 'rarely together',
      high: 'often together',
      cohesion: 'Internal unity',
      cohesionBars: 'Internal cohesion',
      agreeWith: 'Agreement with {faction}',
      mapCaption: 'Proximity shows how often factions vote together.',
      mapHint: 'The axes carry no meaning — only the distance between bubbles does; bubble size is the faction size, its fill its internal cohesion.',
      size: 'Members',
      methodology: 'Agreement is the probability that two randomly chosen voting members — one from each faction — cast the same position (yes/no/abstain), averaged over the filtered roll-call votes. The diagonal is the same within one faction: its internal cohesion. Only roll-call votes count (quorum checks are excluded) and only factions with at least two members are shown. The cycle is set by the header cycle selector.',
    },
  },
  // Paragraphs carry inline links / emphasis and are rendered with v-html
  // (static, author-written markup). A literal '@' breaks the vue-i18n
  // message parser — write it as &#64;.
  // Settlements (§6D) — which places the House names, and which it never does.
  settlements: {
    title: 'Settlements',
    intro: 'Parliament is national, but almost everything it argues about is local. '
      + 'This is which Hungarian settlements are named in plenary, how often and by '
      + 'whom — and which have never been mentioned at all.',
    scope: '{cycle}',
    unit: 'settlements',
    namedOf: 'settlements named (of {total})',
    neverNamed: 'never mentioned once',
    mentionsTotal: 'mentions',
    modeLabel: 'Map view',
    modeAll: 'What is discussed',
    modeBlind: 'Blind spots',
    mapLabel: 'Map of settlement mentions',
    mapFailed: 'The map could not be loaded.',
    mapCaveat: 'The map shows mentions in the plenary transcript for the selected '
      + 'cycle(s), not attention in any wider sense: a village can be well served '
      + 'and never named on the floor of the House. The matching is deliberately '
      + 'conservative, so every count is a lower bound.',
    noGeometry: 'No map data is available for this view.',
    legendBlind: 'no mentions',
    legendBlindOnly: 'never-mentioned settlement',
    legendScale: 'Circle size and shade both follow the number of mentions, on a '
      + 'logarithmic scale.',
    searchPlaceholder: 'Search for a settlement…',
    noResults: 'No settlement matches this filter.',
    sortBy: 'Sort:',
    sortMentions: 'By mentions',
    sortName: 'By name',
    sortElectorate: 'By electorate',
    sortFocus: 'Own-constituency share',
    sortCoverage: 'Constituency coverage',
    clearCounty: 'Clear the {county} filter',
    binsLabel: 'Map binning',
    bins: {
      points: 'Settlements',
      oevk: 'Constituencies',
      h3: 'Segments',
    },
    segmentsUnavailable: 'The segmented view is not available on this server. '
      + 'The settlement map is unaffected.',
    segmentBlindShare: 'never mentioned',
    segmentCounts: '{named} of {total} settlements mentioned',
    segmentShared: 'of which {n} are shared with another constituency',
    segmentOwn: 'has named {named} of {total} settlements in this constituency',
    legendSegmentScale: 'Each segment is an equal-area hexagon; the shade follows the '
      + 'total mentions of the settlements inside it, on a logarithmic scale.',
    legendSegmentBlind: 'The shade is the share of the segment’s settlements that were '
      + 'never mentioned. Hover a segment for the counts behind the share.',
    legendOevkScale: 'Each shape is one single-member constituency: the total mentions '
      + 'of the settlements in it, on a logarithmic scale. Constituencies hold nearly '
      + 'equal numbers of voters, but not equal areas.',
    legendOevkBlind: 'The shade is the share of the constituency’s settlements that '
      + 'were never mentioned. Hover one for the counts and its member.',
    segmentCaveat: 'A segment holds a handful of settlements and some hold only one — '
      + 'over one or two places a share is no longer a share, which is why every '
      + 'segment also reports the counts behind it.',
    oevkCaveat: 'Constituencies hold nearly equal numbers of voters but not equal '
      + 'areas: at the same number, a rural one paints far more of the map than a '
      + 'Budapest one. Five hold a single settlement and nineteen hold three or '
      + 'fewer — there, the counts behind the share are what matter.',
    oevkUnattributed: '“{names}” as a whole ({mentions} mentions) belongs to no single '
      + 'constituency — the capital spans sixteen — so no shape here includes it, '
      + 'though mentions of the individual Budapest districts are included.',
    oevkOverlap: '{settlements} settlements (Debrecen, Szeged, Pécs and the split '
      + 'Budapest districts) lie in more than one constituency. A mention names the '
      + 'place, not the part of it, so it is counted in each: {mentions} mentions '
      + 'appear more than once, and the shapes therefore do not sum to the national '
      + 'total.',
    mentionsShort: 'mentions',
    neverShort: 'never mentioned',
    blindByCounty: 'Where are the blind spots?',
    blindOfTotal: '{blind} of {total} settlements',
    methodology: 'Mentions are found by matching the National Election Office’s '
      + 'official settlement register against the transcript, handling Hungarian '
      + 'case endings and the adjectival form (Kaposváron, Kaposvárra, kaposvári). '
      + 'Procedural and chairing speeches are excluded, as they are from every '
      + 'other statistic here. Settlement names that collide with everyday words or '
      + 'personal names (Baj = “trouble”, Alap = “fund”, Varga = also an MP) count '
      + 'only with corroboration: a place-marking ending or a place word in the '
      + 'sentence, and never inside a recognised person or organisation name. Counts '
      + 'are therefore lower bounds, and a blind spot means “no mention found”, not '
      + '“provably never said”.',
    sourceNote: 'Settlement list and constituency mapping: {source}. The map points '
      + 'are OpenStreetMap’s own place nodes (ODbL), so a dot sits exactly where the '
      + 'basemap prints the name. The mentions come from the parlament.hu transcripts.',
    notFound: 'No such settlement.',
    electorate: '{n} registered voters',
    inSpeeches: 'in speeches',
    bySpeakers: 'speakers',
    between: 'First on {first}, most recently on {last}.',
    neverInScope: 'Never mentioned in the selected cycle.',
    neverExplain: 'This page is the blind spot: the settlement exists, we simply '
      + 'found no reference to it in the plenary transcript. Selecting another cycle '
      + 'may change the result.',
    ambiguousCue: 'This settlement’s name collides with an everyday word or a '
      + 'personal name, so a mention only counts when the sentence unambiguously '
      + 'speaks of a place. The number here is therefore especially conservative.',
    ambiguousSuffix: 'This settlement’s name can collide with another word, so a '
      + 'mention only counts with a place-marking ending or a place word beside it.',
    representedBy: 'Who represents it?',
    repUnknownForCycle: 'We have no record of the member for {list} in this cycle. '
      + 'An earlier cycle’s member is not shown instead: boundaries are redrawn '
      + 'between elections, so that is no longer the same territory.',
    constituencyNote: 'The single-member constituency mapping is the data of the '
      + 'election that produced the current map; boundaries are redrawn between '
      + 'elections. MPs elected from the national list represent this place too.',
    constituencyOnly: 'Constituency: {list}.',
    trendCaption: 'Mentions per year',
    whoNamedIt: 'Who mentioned it?',
    citations: 'The sentences themselves',
    watch: 'watch',
    searchFor: 'Search the transcript for “{name}”',
    repsTitle: 'Do MPs talk about their own constituency?',
    repsIntro: 'Two separate measures for every MP elected in a single-member '
      + 'constituency: how much of their place-talk is about their own seat, and how '
      + 'many of their constituency’s settlements they have ever named.',
    repsCaveat: 'This is a fact, not a league table of diligence. A minister speaks '
      + 'to the whole country, an inner-city member has no village to name, and a low '
      + 'share is not a dereliction. MPs elected from a national or county list are '
      + 'absent from this list — they have no constituency to measure against, and '
      + 'zero is not the same as “not applicable”.',
    repsFloor: 'with at least {n} settlement mentions',
    repsUnit: 'representatives',
    repsEmpty: 'No such data for this cycle.',
    repsMethodology: '“Own constituency” is the share of the MP’s settlement '
      + 'mentions that fall inside their own seat; “coverage” is the share of their '
      + 'constituency’s settlements they have ever named. The two are kept apart on '
      + 'purpose: a high share with low coverage means they talk about one of their '
      + 'towns a great deal. Budapest as a whole counts towards neither (it spans 16 '
      + 'constituencies, so it says nothing about an own seat), while its districts '
      + 'do. The seat is the one the MP actually held in that cycle.',
    colRep: 'Representative',
    colConstituency: 'Constituency',
    colFocus: 'Own seat',
    colCoverage: 'Coverage',
    colMentions: 'Mentions',
    profileTitle: 'Settlements',
    profileFocus: 'of their mentions are their own constituency',
    profileCoverage: 'of their constituency’s settlements named',
    profileNoOwn: 'No single-member constituency, so the own-constituency measures '
      + 'do not apply.',
    profileTop: 'Most-mentioned settlements',
    profileOwnTag: 'own seat',
    profileEmpty: 'Mentioned no settlement in the selected cycle.',
  },
  about: {
    title: 'About the project',
    body1: '<strong>Parlamonitor</strong> is an application by K-Monitor that presents the work of the Hungarian National Assembly in a straightforward way, based on the data published on the <a href="https://www.parlament.hu/" target="_blank" rel="noopener">Assembly’s website</a>. It makes the key information on representatives, votes and tabled motions available in an easy-to-scan, searchable form, and publishes figures and analyses derived from it.',
    body2: 'The aim of the project is to bring parliamentary work closer to citizens and make it easier to follow who takes part in legislation, on what issues and how — and to encourage as many people as possible to speak up, express their views and organise around the causes that matter to them.',
    body3: 'By selectively republishing parts of the data published on the Assembly’s website, <strong>Parlamonitor</strong> helps disseminate public-interest data and make it better known, thereby contributing to a more transparent exercise of public power, and enabling journalists, researchers and interested citizens to examine national political and policy debates, and the state’s law-making activity, on the basis of factual information.',
    body4: 'The current version of the site (published in July 2026) is the result of a first, experimental phase; over the coming months we will extend it based on user feedback and with further features envisaged by K-Monitor. Feedback about the site is <a href="https://www.partimap.eu/hu/p/Parlamonitor-visszajelzes/" target="_blank" rel="noopener">welcome here</a>.',
    figyusz: 'To follow parliamentary documents by topic, use the <a href="https://figyusz.k-monitor.hu/" target="_blank" rel="noopener"><em>Figyusz!</em></a> alerting service.',
    dataTitle: 'Where the data comes from',
    dataBody1: 'The Hungarian National Assembly publishes an exemplary amount of data on representatives, parliamentary debates and votes, and the legislative process — but much of it is hard to search or analyse. K-Monitor partially republishes data published on the <a href="https://parlament.hu" target="_blank" rel="noopener">Assembly’s website</a>, or points users to it (for videos, for example). Although the Assembly commendably maintains a <a href="https://www.parlament.hu/web/guest/alkalmazasok" target="_blank" rel="noopener">Web API service</a> for developers that exposes many data types in machine-readable form, the service has significant gaps, so we do not use it as a data source in the Parlamonitor project.',
    dataBody2: 'Many of the API’s endpoints cannot return historical data. For example, according to the documentation the <strong>kepviselok, iromanyok, iromany, szoszolok, bizottsagok, bizottsag</strong> endpoints accept no cycle parameter. Beyond that, essential fields are missing from the API: one is the return of non-standalone motions, which the documentation describes as <strong>Reserved for later development</strong>. For speeches a cycle parameter can be given, but the results do not include the video URL, which is indispensable for our use. Another weakness we observed while using the API is that the <strong>iromanyok</strong> endpoint simply returns every document of the current cycle in a single XML file, with no way to filter by time. When tracking newly published reasonings, this generates needlessly large traffic on every update.',
    dataBody3: 'Because of the shortcomings listed above, we obtain the data the project needs by scraping certain content from the Assembly’s website and make it available on Parlamonitor. In our view this method of access is consistent with the terms of use of the Assembly’s website, with the freedom-of-information act, and with the rules on the re-use of public sector information. The source code of the software we built is freely available to anyone; we hope it also contributes to the further development of the Assembly’s website.',
    sourcesTitle: 'Sources and credits',
    sourcesBody1: 'A significant part of the site’s content comes from parlament.hu, the website operated by the National Assembly.',
    sourcesBody2: 'Synchronising the transcripts with the recordings was inspired by the <a href="https://openparliament.tv/startseite/" target="_blank" rel="noopener">Open Parliament TV</a> project.',
    sourcesBody3: 'Lemmatisation for the word clouds is done by the <a href="https://huspacy.github.io/models/index_md/" target="_blank" rel="noopener">md</a> and <a href="https://huspacy.github.io/models/index_trf/" target="_blank" rel="noopener">trf</a> models of <a href="https://github.com/huspacy/huspacy" target="_blank" rel="noopener">huspacy</a>. Created by: György Orosz, Gergő Szabó, Péter Berkecz, Zsolt Szántó, Richárd Farkas',
    sourcesModelsIntro: 'The models’ sources:',
    sourcesModel1: 'UD Hungarian Szeged (Richárd Farkas, Katalin Simkó, Zsolt Szántó, Viktor Varga, Veronika Vincze (MTA-SZTE Research Group on Artificial Intelligence))',
    sourcesModel2: 'NYTK-NerKor Corpus (Eszter Simon, Noémi Vadász (Department of Language Technology and Applied Linguistics))',
    sourcesModel3: 'Szeged NER Corpus (György Szarvas, Richárd Farkas, László Felföldi, András Kocsor, János Csirik (MTA-SZTE Research Group on Artificial Intelligence))',
    sourcesModel4: 'Hungarian lg Floret vectors (Szeged AI)',
    sourcesModel5: 'huBERT base model (cased) (Dávid Márk Nemeskey (SZTAKI-HLT))',
    sourcesBody4: 'When linking representatives and other entities, and when enriching their metadata, we use the data of <a href="https://www.wikidata.org/" target="_blank" rel="noopener">Wikidata</a> and <a href="https://hu.wikipedia.org/" target="_blank" rel="noopener">Wikipedia</a>.',
    sourcesBody5: 'To download videos we load the <a href="https://github.com/ffmpegwasm/ffmpeg.wasm" target="_blank" rel="noopener">ffmpeg.wasm</a> library, which we owe to <a href="https://github.com/jeromewu" target="_blank" rel="noopener">Jerome Wu</a> and which builds on the <a href="https://www.ffmpeg.org/" target="_blank" rel="noopener">FFmpeg</a> project. We also use the Liberation Sans font for the burned-in subtitles.',
    sourcesBody6: 'The map of the representative finder uses a library called <a href="https://leafletjs.com/" target="_blank" rel="noopener">Leaflet</a>, which loads the map of <a href="https://www.openstreetmap.org" target="_blank" rel="noopener">OpenStreetMap</a>. Matching individual constituencies to settlements, and their polygons, is based on data extracted from vtr.valasztas.hu.',
    sourcesBody7: 'To synchronise texts with videos we used OpenAI’s <a href="https://huggingface.co/openai/whisper-large-v3-turbo" target="_blank" rel="noopener">Whisper</a> model.',
    sourcesBody8: 'The activity chart on representatives’ pages was modelled on the similar chart in GitHub’s user profile interface.',
    sourcesBody9: 'During development we made intensive use of the LLM-based coding assistant Claude Code in order to speed up programming.',
  },
  donate: {
    title: 'Support Parlamonitor',
    description: 'Parlamonitor is a free and independent project, built and maintained by the NGO K-Monitor. If you find it useful, a small donation helps keep it running and improving.',
    amountsLabel: 'Donation amount',
    paypalButton: 'Donate with PayPal',
    moreOptionsButton: 'More ways to support',
    footer: 'Support us',
    close: 'Close',
  },
  footer: {
    aboutHeading: 'Parlamonitor',
    about: 'About us',
    contact: 'Contact',
    lastUpdate: 'Last data update',
    kmonitorHome: 'K-Monitor website',
    instagram: 'K-Monitor on Instagram',
    facebook: 'K-Monitor on Facebook',
    bluesky: 'Parlamonitor on Bluesky',
    feedback: 'Feedback',
  },
  agendaTypes: {
    opening: 'Opening', procedural: 'Procedural', regular: 'Debate', oath: 'Oath', voting: 'Voting',
    rules_of_procedure: 'Rules of procedure', questioning_of_the_government: 'Interpellation',
    qa: 'Immediate question / question', condolence: 'Commemoration',
  },
}
