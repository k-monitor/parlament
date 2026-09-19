// Hungarian — the primary UI language (I18N-1). All user-facing copy lives here.
export default {
  app: {
    title: 'Parlamonitor',
    tagline: 'Tudd meg, mi történik a Parlamentben!',
    taglineSub: 'Keress felszólalásokban, nézd meg, hogyan szavaztak a képviselők, és kövesd végig a törvényjavaslatok sorsát.',
    skipToContent: 'Ugrás a tartalomra',
    loading: 'Betöltés…',
    error: 'Hiba történt az adatok betöltésekor.',
    retry: 'Újrapróbálás',
    notFound: 'A keresett oldal nem található.',
    backHome: 'Vissza a főoldalra',
    source: 'Forrás',
    sourceNote: 'Az adatok forrása a parlament.hu; a feldolgozást a Parlamonitor végzi.',
    openData: 'Nyílt adat',
    terms: 'Felhasználási feltételek',
    dataTerms: 'A parlament.hu adatainak felhasználási feltételei',
    pager: {
      nav: 'Lapozás',
      prev: 'Előző oldal',
      next: 'Következő oldal',
      status: '{page}. / {total} oldal',
    },
  },
  nav: {
    home: 'Főoldal',
    menu: 'Főmenü',
    submenu: 'almenü',
    search: 'Keresés',
    sessions: 'Ülésnapok',
    // A Felszólalók oldal a képviselőket, a nemzetiségi szószólókat és az egyéb
    // felszólalókat egy listában mutatja, kategóriacsipekkel (REP-1).
    representatives: 'Felszólalók',
    // A két mandátum nélküli kategória neve: a csipeken és a profilról visszavezető
    // linken is ez áll.
    advocates: 'Nemzetiségi szószólók',
    speakers: 'Egyéb felszólalók',
    officials: 'Tisztségviselők',
    portfolios: 'Tárcák',
    factions: 'Frakciók',
    bills: 'Törvényjavaslatok',
    // A szekció harmadik, gyűjtőfüle: minden iromány típus egy listában
    // (BILL-9), a törvényjavaslatokkal és a kérdésekkel együtt.
    documents: 'Minden iromány',
    // Két fül olvassa ugyanerről a névről: a Törvényjavaslatok szekció kérdés-
    // listája (BILL-13) és az Elemzések Sankey-ja (BILL-11) — szándékosan.
    questions: 'Kérdések',
    votes: 'Szavazások',
    // Elemzések (§4E): a szekció neve a felső sávban, és — `analysesIndex` — a
    // saját fülsorának első füle, ami a szekció nyitóoldalára visz vissza.
    analyses: 'Elemzések',
    analysesIndex: 'Áttekintés',
    cohesion: 'Frakcióelemzés',
    // Témák (TOPIC-9): a szakpolitikai témák megoszlása. A fül rövid neve áll
    // itt; az oldal saját címe ennél beszédesebb.
    topics: 'Témák',
    interjections: 'Közbeszólások',
    // Települések (§6D): a térkép és — a fül alatti oldalként — a saját körzetre
    // vonatkozó mutatók (TEL-9).
    settlements: 'Települések',
    settlementReps: 'Saját körzet',
    about: 'A projektről',
  },
  share: {
    label: 'Megosztás',
    menu: 'Megosztási lehetőségek',
    native: 'Megosztás…',
    facebook: 'Facebook',
    x: 'X',
    bluesky: 'Bluesky',
    copy: 'Hivatkozás másolása',
    copied: 'Vágólapra másolva!',
  },
  embed: {
    label: 'Beágyazás',
    title: 'Ábra beágyazása',
    cycleNote: 'A beágyazott kód a(z) {cycle} adatait mutatja.',
    copy: 'Kód másolása',
    copied: 'Másolva!',
    preview: 'Előnézet ↗',
    openInteractive: 'Interaktív változat ↗',
    noData: 'Nincs megjeleníthető adat.',
  },
  cycle: {
    label: 'Ciklus',
    all: 'Összes',
    count: '{n} ciklus',
    multiHint: 'Több ciklus is kiválasztható.',
    scope: '{cycle} adatai',
    scopeAll: 'Összes ciklus adatai',
  },
  home: {
    searchPlaceholder: 'Keress egy kifejezésre a felszólalásokban…',
    searchButton: 'Keresés',
    statsLead: 'Böngészd {year} óta a Magyar Országgyűlés munkájának adatait!',
    statsLeadNoYear: 'Böngészd a Magyar Országgyűlés munkájának adatait!',
    stats: { sessions: 'ülésnap', speeches: 'felszólalás', sentences: 'mondat', representatives: 'képviselő' },
    exploreSearch: 'Szöveg kereső',
    exploreSearchDesc: 'Keress bármilyen kifejezésre a jegyzőkönyvben, és ugorj egyenesen az elhangzás pillanatára a videóban!',
    exploreReps: 'Képviselők és statisztikák',
    exploreRepsDesc: 'Böngészd a képviselőket, nézd meg felszólalásaikat és aktivitásukat!',
    exploreSessions: 'Ülésnapok böngészése',
    exploreSessionsDesc: 'Olvasd végig egy ülésnap jegyzőkönyvét napirendi pontokra bontva!',
    examplesTitle: 'Példakeresések',
    examplesLead: 'Kattints egy témára a teljes keresés megnyitásához; a diagram az adott kifejezés időbeli előfordulását mutatja.',
    examplesEmpty: 'Nincs találat ebben a ciklusban.',
    examplesMore: 'További témák megtekintése',
  },
  search: {
    title: 'Keresés a felszólalásokban',
    placeholder: 'Kifejezés vagy „pontos kifejezés”…',
    button: 'Keresés',
    results: 'találat',
    resultsCapped: 'több mint {n} találat',
    tooSlow: 'A keresés túl sok felszólalást érintett, ezért megszakadt. Próbálj hosszabb vagy pontosabb keresőszót, vagy szűkítsd a ciklust, illetve az időszakot.',
    noResults: 'Nincs találat a megadott feltételekre.',
    hint: 'Tipp: idézőjellel pontos kifejezésre kereshetsz, pl. „tisztelt ház”.',
    filters: 'Szűrők',
    clearFilters: 'Szűrők törlése',
    period: 'Ciklus',
    dateFrom: 'Dátumtól',
    dateTo: 'Dátumig',
    speaker: 'Felszólaló',
    // A felszólalószűrő (SEA-3) és a hozzá tartozó javaslatok (SEA-7).
    speakerPlaceholder: 'Név…',
    speakerHint: 'Kezdj el írni egy nevet.',
    speakerNoMatch: 'Nincs ilyen nevű felszólaló.',
    speakerClear: 'Felszólalószűrő törlése',
    speakerSpeeches: '{n} felszólalás',
    speakerSuggest: 'Szűrés felszólalóra:',
    // Keresőszó nélkül, csak felszólalóra szűrve a találatok a felszólalásai.
    trendCaptionSpeaker: '{name} felszólalásai az időben',
    faction: 'Frakció',
    agendaType: 'Napirend típusa',
    all: 'Mind',
    // Több értéket is felvevő szűrő (MultiSelect): a gomb felirata és a panel súgója.
    selectedCount: '{n} kiválasztva',
    multiHint: 'Több érték is kiválasztható.',
    watch: 'Megtekintés',
    on: '·',
    sort: 'Rendezés:',
    sortRelevance: 'Relevancia',
    sortNewest: 'Legújabb elöl',
    sortOldest: 'Legrégebbi elöl',
    trendCaption: 'A(z) „{q}” előfordulása az időben',
    trendHint: 'Kattints egy oszlopra az adott időszakra szűkítéshez.',
    breakdown: 'Találatok megoszlása',
    breakdownFactions: 'A(z) „{q}” találatok frakciónként',
    breakdownSpeakers: 'A(z) „{q}” találatok felszólalónként',
    cycleScope: 'A keresés a(z) {cycle} felszólalásaira szűkül.',
    cycleScopeAll: 'váltás minden ciklusra',
  },
  viewer: {
    transcript: 'Jegyzőkönyv',
    noTranscript: 'Ehhez a felszólaláshoz nem érhető el szövegezett jegyzőkönyv.',
    videoOnly: 'Csak videó érhető el.',
    play: 'Lejátszás innen',
    playBtn: 'Lejátszás', pauseBtn: 'Szünet', mute: 'Némítás', unmute: 'Hang bekapcsolása',
    fullscreen: 'Teljes képernyő', seek: 'Tekerés a felszólaláson belül', volume: 'Hangerő',
    prevSpeech: 'Előző felszólalás',
    nextSpeech: 'Következő felszólalás',
    estimatedTiming: 'Becsült időzítés',
    estimatedTimingTip: 'A videó időzítése pozícióalapú becslés (karakterarányos), ezért közelítő pontosságú.',
    viewOnParlament: 'Megtekintés a parlament.hu-n',
    license: 'Felhasználás',
    agenda: 'Napirendi pont',
    speaker: 'Felszólaló',
    copyLink: 'Hivatkozás másolása',
    linkCopied: 'Hivatkozás vágólapra másolva',
    sittingDay: 'ülésnap',
    backToSession: 'Vissza az ülésnaphoz',
    speechType: 'Felszólalás típusa',
    autoplayNext: 'Automatikus továbblépés',
    autoplayNextTip: 'A felszólalás végén automatikusan a következőre lép, és tovább játssza.',
  },
  // Felszólalás-annotáció: olvashatóság (LIX) és szókincsgazdagság (MATTR).
  // A chipen csak az áll, hogy a felszólalás a Parlament mediánjához képest hol van: a
  // nyers számok önmagukban értelmezhetetlenek (a klasszikus svéd LIX-címkék a
  // 6-os hosszúszó-küszöbre vannak kalibrálva, a magyar küszöb 8; a MATTR meg
  // puszta arányszám), ezért a szám, a kvintilise és a mögötte lévő darabszámok a
  // tooltipbe kerültek. A `*VsShort` a sűrű ülésnap-listáé.
  metrics: {
    lixName: 'Olvashatóság (LIX)',
    lixVs: {
      easier: 'könnyebben olvasható',
      typical: 'átlagos',
      harder: 'nehezebben olvasható',
    },
    lixVsShort: { easier: 'könnyebb', typical: 'átlagos', harder: 'nehezebb' },
    vsTip: 'A Parlament összes mért felszólalásának mediánjához viszonyítva – a középső '
      + 'ötöd számít átlagosnak.',
    lixTip: 'A hosszú szavak aránya és a mondathossz alapján számoljuk.',
    lixScore: 'LIX {value} – a Parlament felszólalásai közül {band}',
    lixCounts: '{words} szó · {sentences} mondat · {perSentence} szó/mondat · '
      + '{longShare}% hosszú szó (8 betűnél hosszabb)',
    lixBand: {
      'very-easy': 'a legkönnyebb ötödben',
      easy: 'a második legkönnyebb ötödben',
      average: 'a középső ötödben',
      hard: 'a második legnehezebb ötödben',
      'very-hard': 'a legnehezebb ötödben',
    },
    mattrName: 'Szókincsgazdagság (MATTR)',
    mattrVs: {
      less: 'kevésbé változatos szókincs',
      typical: 'átlagos',
      more: 'változatosabb szókincs',
    },
    mattrVsShort: { less: 'kevésbé változatos', typical: 'átlagos', more: 'változatosabb' },
    mattrTip: 'Hány százalékban különbözőek a szótövek egy {window} szavas ablakon '
      + 'belül. A szóalakokat szótőre visszavezetve mérjük, hogy a magyar ragozás '
      + 'ne látszódjon gazdagabb szókincsnek.',
    mattrScore: 'MATTR {value} – a Parlament felszólalásai közül {band}',
    mattrCounts: '{types} különböző szótő · {tokens} szó',
    mattrBand: {
      'very-low': 'a legkevésbé változatos ötödben',
      low: 'a második legkevésbé változatos ötödben',
      average: 'a középső ötödben',
      high: 'a második legváltozatosabb ötödben',
      'very-high': 'a legváltozatosabb ötödben',
    },
  },
  // CAP szakpolitikai témák (TOPIC-1..7). A chipen csak a téma neve áll; minden, ami
  // árnyalja — hogy a felszólalás mekkora részét fedi le, mi ellen "nyert", mennyi
  // maradt bizonytalan, és melyik modell milyen küszöbbel döntött — a kattintásra
  // nyíló panelbe került. A témanevek a CAP magyar kódkönyvének elnevezéseit követik.
  // A kétsávos ábra szövegei. Külön névtér, mert az ábrát az oldal és a
  // beágyazott változat is használja (§4C).
  topicMix: {
    speeches: 'Felszólalások',
    bills: 'Irományok',
    tip: {
      speech: '{topic} – felszólalások: a besorolt szakpolitikai szöveg {pct}-a '
        + '({words} szó), {n} felszólalás témája.',
      bill: '{topic} – irományok: a besorolt szakpolitikai szöveg {pct}-a '
        + '({words} szó), {n} iromány témája.',
    },
  },
  topics: {
    chipTitle: 'Téma: {topic} – kattintson a részletekért',
    panelLabel: 'A felszólalás témája',
    share: 'A besorolt szöveg {pct}-a erről szól ({n} szövegrész).',
    alsoAbout: 'Emellett szóba került',
    coverage: 'A felszólalás szövegének {pct}-át lehetett kellő biztonsággal '
      + 'besorolni; a többiről a modell nem nyilatkozik.',
    otherShare: 'A besorolt rész {pct}-a nem szakpolitikai (üdvözlés, ügyrend, '
      + 'személyes megjegyzés).',
    method: 'Gépi besorolás a ParlaCAP modellel, bekezdésenként, {threshold}%-os '
      + 'megbízhatósági küszöbbel. Tájékoztató jellegű, nem hivatalos minősítés.',
    // Az irományokra ugyanaz a modell fut, de nem felszólalásra: a szöveg a
    // benyújtott dokumentumból származik, így a szóhasználat is más. Csak az
    // eltérő mondatok szerepelnek itt; a többit a fenti közös kulcsok adják.
    bill: {
      panelLabel: 'Az iromány témája',
      coverage: 'Az iromány szövegének {pct}-át lehetett kellő biztonsággal '
        + 'besorolni; a többiről a modell nem nyilatkozik.',
      otherShare: 'A besorolt rész {pct}-a nem szakpolitikai tartalom (fejléc, '
        + 'iktatás, aláírás, eljárási formula).',
      method: 'Gépi besorolás a ParlaCAP modellel, a benyújtott dokumentum '
        + 'szövege alapján, szövegrészenként, {threshold}%-os megbízhatósági '
        + 'küszöbbel. Tájékoztató jellegű, nem hivatalos minősítés.',
    },
    // A Témák elemzés (TOPIC-9) oldalszövegei. Ugyanabban a névtérben, mint a
    // csip és a témanevek: egy téma ugyanazt jelenti mindkét helyen.
    page: {
      title: 'Miről szól a Parlament?',
      lead: 'Az Országgyűlés napirendje szakpolitikai témák szerint: miről beszélnek '
        + 'a plenáris ülésen, és mi kerül irományként a Ház elé. A kettő nem '
        + 'ugyanaz a lista — a különbség maga is eredmény.',
      help1: 'Minden felszólalást és minden olvasható iromány szövegét gépi '
        + 'osztályozó sorolja be a CAP nemzetközi kódrendszerének 21 '
        + 'szakpolitikai témájába, bekezdésenként, {threshold}%-os megbízhatósági '
        + 'küszöbbel. Amiben a modell nem elég biztos, arról inkább nem mond semmit.',
      help2: 'A sávok a besorolt szakpolitikai szöveg megoszlását mutatják szavak '
        + 'szerint. Az ügyrendi, udvariassági és személyes részek („egyéb”) nem '
        + 'számítanak bele, az ülésvezetői felszólalások pedig egyáltalán nem '
        + 'kerülnek a modell elé.',
      unclassified: 'Ebben a telepítésben nincsenek témabesorolások.',
      coverageSpeech: 'a felszólalások {pct}-a kapott témát ({n} / {total})',
      // Az irományoknál a nevező nem az összes iromány, hanem amelyiknek
      // egyáltalán van olvasható szövege (TOPIC-8) — ezt ki kell mondani.
      coverageBill: 'az olvasható szövegű irományok {pct}-a ({n} / {total})',
      sortBy: 'Rendezés',
      chartCaption: 'A szakpolitikai témák megoszlása a besorolt szövegben',
      clickHint: 'Kattintson egy témára: kik beszélnek róla, melyik frakció, '
        + 'és hogyan változott az évek során.',
      close: 'Bezárás',
      leadSpeech: 'A plenáris ülés besorolt szakpolitikai szövegének {pct}-a szól '
        + 'erről, és {n} felszólalásnak ez a témája.',
      leadNoSpeech: 'Erről a témáról a vizsgált időszakban nem hangzott el elég '
        + 'biztosan besorolható felszólalás.',
      leadBill: 'Az irományok besorolt szövegéből {pct} jut rá, {n} iromány témája.',
      trendCaption: '{topic}: a plenáris szöveg hány százaléka szólt erről évente',
      factions: 'Melyik frakció beszél róla',
      factionsNote: 'A sáv azt mutatja, hogy a frakció saját besorolt szakpolitikai '
        + 'szövegéből mennyi jut erre a témára — így a kis frakció beszédtémája is '
        + 'látszik. Alatta az, hogy a témáról elhangzottakból mennyi az övé.',
      shareOfTopic: 'a témáról elhangzottak {pct}-a',
      noFaction: 'Frakció nélkül',
      speakers: 'Kik beszélnek róla',
      speakersNote: 'A témáról legtöbbet beszélők, a téma szövegéből való '
        + 'részesedésükkel.',
      noDetail: 'Ehhez a témához nincs bontás a kiválasztott időszakban.',
      openBills: 'Irományok ezzel a témával',
      method: 'Gépi besorolás a ParlaCAP modellel, bekezdésenként, {threshold}%-os '
        + 'megbízhatósági küszöbbel. Tájékoztató jellegű, nem hivatalos minősítés.',
    },
    names: {
      Macroeconomics: 'Makrogazdaság',
      'Civil Rights': 'Emberi és állampolgári jogok',
      Health: 'Egészségügy',
      Agriculture: 'Mezőgazdaság',
      Labor: 'Munkaügy és foglalkoztatás',
      Education: 'Oktatás',
      Environment: 'Környezetvédelem',
      Energy: 'Energiaügy',
      Immigration: 'Bevándorlás',
      Transportation: 'Közlekedés',
      'Law and Crime': 'Jog és bűnügyek',
      'Social Welfare': 'Szociális ügyek',
      Housing: 'Lakhatás és városfejlesztés',
      'Domestic Commerce': 'Belkereskedelem és bankügy',
      Defense: 'Honvédelem',
      Technology: 'Tudomány és technológia',
      'Foreign Trade': 'Külkereskedelem',
      'International Affairs': 'Külügy és nemzetközi kapcsolatok',
      'Government Operations': 'Kormányzat és közigazgatás',
      'Public Lands': 'Állami földterületek és vízügy',
      Culture: 'Kultúra',
      Other: 'Egyéb, nem szakpolitikai',
    },
  },
  clipExport: {
    button: 'Videó letöltése',
    segmentButton: 'Ez a mondat letöltése videórészletként',
    title: 'Videórészlet letöltése',
    close: 'Bezárás',
    segment: 'Szakasz',
    from: 'Ettől',
    to: 'Eddig',
    length: 'Hossz',
    subtitles: 'Felirat',
    subNone: 'Felirat nélkül',
    subNoneHint: 'csak a videó',
    subSoft: 'Kapcsolható felirat',
    subSoftHint: 'gyors, a videó nem kódolódik újra',
    subBurn: 'Ráégetett felirat',
    subBurnHint: 'a képbe égetve; közösségi videóhoz',
    wholeSpeech: 'Teljes felszólalás',
    keepOpen: 'Ne zárd be ezt az ablakot, amíg elkészül.',
    watermark: 'Parlamonitor vízjel',
    watermarkHint: 'a logó a jobb felső, a dátum a bal felső sarokba kerül (a videó ilyenkor újrakódolódik)',
    noTextNote: 'Ehhez a felszólaláshoz nincs szövegezett jegyzőkönyv, ezért csak felirat nélkül tölthető le.',
    reencodeWarn: 'Ez a beállítás (ráégetett felirat, vízjel vagy álló formátum) újrakódolja a videót, ezért a letöltés lassabb lehet.',
    mobileWarn: 'Úgy tűnik, mobileszközt használsz. A videó feldolgozása a böngészőben itt jóval lassabb lehet.',
    format: 'Formátum',
    orient_landscape: 'Fekvő',
    orient_portrait: 'Álló',
    orientHint_landscape: 'eredeti',
    orientHint_portrait: 'TikTok / Reels',
    quality: 'Minőség',
    quality_low: 'Alacsony',
    quality_medium: 'Közepes',
    quality_high: 'Magas',
    start: 'Letöltés indítása',
    cancel: 'Mégse',
    cancelled: 'A letöltés megszakítva.',
    phaseLoading: 'Videómotor betöltése…',
    phaseFetching: 'Videó letöltése…',
    phaseEncoding: 'Fájl összeállítása…',
    phaseEncodingBurn: 'Videó újrakódolása felirattal…',
    ready: 'A videórészlet elkészült.',
    download: 'MP4 mentése',
    saved: 'Mentés elindult',
    savedNote: 'A letöltés elindult — a fájlt a böngésződ letöltései között találod.',
    another: 'Új részlet',
    provenance: 'A fájl a parlament.hu nyilvános felvételéből és a hivatalos jegyzőkönyvből készül, a böngésződben. Forrás: Magyar Országgyűlés.',
    unsupported: 'A böngésződ nem támogatja a böngészőn belüli videóexportot.',
    failed: 'A videórészlet elkészítése nem sikerült. Kérlek, próbáld újra.',
  },
  entity: {
    uncertain: 'Bizonytalan találat',
    profile: 'Profil',
    kmonitor: 'K-Monitor adatbázis',
    wikipedia: 'Wikipédia',
    timeMarker: 'Időpont a jegyzőkönyvben',
  },
  sessions: {
    title: 'Ülésnapok',
    date: 'Dátum',
    sitting: 'Ülésnap',
    speeches: 'felszólalás',
    agendaItems: 'napirendi pont',
    open: 'Megnyitás',
    agenda: 'Napirend',
    duration: 'Időtartam',
    dayNav: 'Váltás másik ülésnapra',
    prevDay: 'Előző ülésnap',
    nextDay: 'Következő ülésnap',
    wordcloud: 'Miről volt szó ezen a napon?',
    wordcloudCaption: 'A napra leginkább jellemző szavak: amelyek ezen a napon gyakoriak, de a ciklus többi ülésnapján ritkák (TF·IDF). A szavak szótövükre vonatkoznak (a ragozott alakok összevonva), a felismert nevek (személyek, helyek, szervezetek) dőlten jelennek meg. Az ülésvezetés és a gyakori töltelékszavak kihagyva. Kattintásra rákeres az adott napra.',
    wordcloudCount: 'előfordulás',
    wordcloudEntity: 'név',
    topicsPreview: 'A nap témái',
    topSpeakers: 'Ki beszélt a legtöbbet ezen a napon?',
    topSpeakersCaption: 'A képviselők összes felszólalási ideje szerint ezen az ülésnapon (az ülésvezetői és rendészeti felszólalások nélkül). A névre kattintva a képviselő profilja nyílik meg.',
    newWords: 'Mely szavak hangzottak el először?',
    metricsLabel: 'Beszédmetrikák',
    metricsShow: 'Beszédmetrikák',
    metricsHide: 'Beszédmetrikák elrejtése',
    metricsCaption: 'Felszólalásonként két összehasonlítás: mennyire nehéz olvasni '
      + '(LIX – a hosszú szavak aránya és a mondathossz), és mennyire változatos a '
      + 'szókincse (MATTR – mennyire különbözőek a szótövek). Mindkettő csak azt '
      + 'mondja meg, hogy a felszólalás a Parlament összes felszólalásának mediánjához '
      + 'képest hol van, mert egyik pontszám sem értelmezhető abszolút skálán; a '
      + 'chipre húzva a szám is előjön. Csak a kellően hosszú, érdemi '
      + 'felszólalásokra számoljuk ki – az ülésvezetői és a rövid hozzászólásokon '
      + 'nem jelenik meg. Alapból nincs bekapcsolva: ha itt bekapcsolod, az egyes '
      + 'felszólalások oldalán is megjelenik, és megjegyezzük ezen az eszközön.',
    newWordsCaption: 'Ezek a szótövek ezen az ülésnapon hangzottak el először a parlamentben – korábban, a megelőző ciklusokban sem mondta ki őket senki (a rendelkezésre álló jegyzőkönyvek alapján). A neveket és a nagybetűs (tulajdonnévi) szavakat kihagytuk. A szám az adott napi előfordulást jelzi. Kattintásra rákeres az adott napra.',
    showTranscript: 'Jegyzőkönyv megjelenítése',
    hideTranscript: 'Jegyzőkönyv elrejtése',
    openViewer: 'Videó és jegyzőkönyv megnyitása',
    // A napnak van felvétele, de az Országgyűlés még nem vágta felszólalásokra:
    // a lejátszó a nap teljes videóját nyitja meg, nem ennek a felszólalásnak a
    // részletét — a súgó ezért mást ígér, mint az `openViewer`.
    openDayVideo: 'A nap teljes felvételének megnyitása',
    transcriptLoading: 'Jegyzőkönyv betöltése…',
    transcriptLoadError: 'A jegyzőkönyv betöltése nem sikerült.',
    upcoming: 'Hamarosan',
    upcomingNote: 'Az ülésnap már szerepel az Országgyűlés napirendjén – a felvétel és a jegyzőkönyv hamarosan elérhető lesz.',
    notProcessed: 'Ez az ülésnap még nincs feldolgozva.',
    notReady: 'Feldolgozás alatt',
    notReadyNote: 'Ezt az ülésnapot az Országgyűlés még nem tette teljesen elérhetővé: a felszólalások időpontjai, a videó és a jegyzőkönyv még nem érhetők el. Amint közzéteszik, automatikusan megjelennek itt.',
    // Ugyanaz az állapot, de a nap teljes felvétele már közzé van téve: a ▶ ikon
    // ezt nyitja meg (felszólalásokra vágva még nincs, jegyzőkönyv sincs).
    notReadyVideoNote: 'Ezt az ülésnapot az Országgyűlés még nem tette teljesen elérhetővé: a felszólalások időpontjai és a jegyzőkönyv még nem érhetők el. A nap teljes videófelvétele viszont már megtekinthető – a ▶ ikonnal nyitható meg. A többi adat, amint közzéteszik, automatikusan megjelenik itt.',
    // Az ülésnap böngészhető, de az Országgyűlés részletekben teszi közzé (a
    // jegyzőkönyv napokkal késik a felvétel után, a videó felszólalásokra vágása
    // is szakaszosan készül el), ezért jelöljük, hogy még nem teljes.
    partial: 'Részben feldolgozva',
    partialNote: 'Az Országgyűlés még nem tette közzé ennek az ülésnapnak minden felszólaláshoz a jegyzőkönyvét vagy a videórészletét. A hiányzó részek, amint megjelennek, automatikusan bekerülnek.',
    // Az Országgyűlés még nem kapcsolta napirendi pontokhoz az ülésnap
    // felszólalásait: ilyenkor egyetlen, időrendi listaként jelenik meg a nap.
    unlistedAgenda: 'Az ülésnap felszólalásai',
  },
  reps: {
    title: 'Felszólalók',
    // A keresőkártya tetején álló váltó: ugyanaz a kérdés név szerint (a lista,
    // REP-1) vagy település szerint („Ki a képviselőm?”, REP-10). A hely szerinti
    // ág címkéje a lookup.title, hogy a kérdés egy helyen legyen megírva.
    searchMode: 'Keresés módja',
    modeName: 'Név szerint',
    // Kategóriacsipek a keresőmező alatt: alapból a képviselők (REP-1).
    category: 'Kategória',
    role: {
      mp: 'Képviselők',
      advocate: 'Nemzetiségi szószólók',
      other: 'Egyéb felszólalók',
      all: 'Összes',
    },
    allNote: 'Egy listában mindenki, aki az Országgyűlésben szerepet kap: a '
      + 'képviselők, a nemzetiségi szószólók, valamint a mandátum nélküli '
      + 'felszólalók – miniszterek, államtitkárok, a köztársasági elnök és '
      + 'meghívott vendégek.',
    searchPlaceholder: 'Képviselő keresése név szerint…',
    faction: 'Frakció',
    constituency: 'Választókerület',
    speeches: 'felszólalás',
    speakingTime: 'beszédidő',
    sortName: 'Név szerint',
    sortSpeeches: 'Felszólalások szerint',
    sortSpeakingTime: 'Beszédidő szerint',
    noResults: 'Nincs a feltételeknek megfelelő képviselő.',
    profile: 'Profil',
    filterByFaction: '{faction} képviselőinek szűrése',
    // Megszűnt mandátumok (REP-14). A ciklus listája mindenkit tartalmaz, aki
    // mandátumot viselt benne; az „aktív” a ciklushoz képest értendő.
    mandateFilter: 'Mandátum',
    mandateAll: 'Mind',
    mandateActive: 'Aktív',
    mandateTerminated: 'Megszűnt',
    mandateFilterNote: 'Az „aktív” a kiválasztott ciklushoz képest értendő: a folyó '
      + 'ciklusban a ma is hivatalban lévőket, lezárt ciklusban azokat jelenti, '
      + 'akiknek a mandátuma kitartott a ciklus végéig.',
    mandateEnded: 'megszűnt mandátum',
    // Nemzetiségi szószólók: mandátum nélkül üléseznek és felszólalnak (REP-9).
    mandate: 'Mandátum',
    advocateFor: '{nationality} nemzetiségi szószóló',
    advocatesUnit: 'szószóló',
    advocateNote: 'A szószólók az Országgyűlés munkájában részt vesznek – felszólalnak és irományokat '
      + 'nyújtanak be –, de nem képviselők: nincs frakciójuk, választókerületük és szavazati joguk.',
    searchAdvocatePlaceholder: 'Szószóló keresése név szerint…',
    noAdvocateResults: 'Nincs a feltételeknek megfelelő szószóló.',
    // Egyéb felszólalók: mandátum nélkül szólaltak fel a Parlamentben (REP-12).
    othersUnit: 'felszólaló',
    otherNote: 'Az Országgyűlés ülésein nemcsak képviselők szólalnak fel: '
      + 'miniszterek és államtitkárok – akik gyakran nem képviselők –, a '
      + 'köztársasági elnök, az önálló szervek vezetői és meghívott vendégek is. '
      + 'Ők itt szerepelnek: nincs frakciójuk, választókerületük és szavazati '
      + 'joguk, a tisztségük azonosítja őket.',
    searchOtherPlaceholder: 'Felszólaló keresése név szerint…',
    noOtherResults: 'Nincs a feltételeknek megfelelő felszólaló.',
  },
  // Tisztségviselők (REP-11) — az Országgyűlés hivatalos, 1990-ig visszamenő
  // nyilvántartása, megbízatásonként egy sor.
  officials: {
    title: 'Tisztségviselők',
    intro: '',
    unit: 'megbízatás',
    searchPlaceholder: 'Keresés név vagy tisztség szerint…',
    category: 'Tisztség típusa',
    status: 'Állapot',
    statusCurrent: 'Jelenleg is betölti',
    statusPast: 'Korábbi',
    // A ciklusra szűkített nézet a ciklus *alatt betöltött* megbízatásokat mutatja,
    // nem csak a benne kezdődőeket – ezért mondja ki a felirat, hogy melyikről van szó.
    scopeHeld: 'a(z) {cycle} ciklus alatt betöltve',
    scopeStarted: 'a(z) {cycle} ciklusban kezdődött',
    startedHint: 'ebből {count} kezdődött ebben a ciklusban',
    startedShowAll: 'mind a(z) {count} megjelenítése',
    started: 'Kezdete',
    startedAll: 'Mind',
    startedInCycle: 'Ebben a ciklusban kezdődött',
    inOffice: 'hivatalban',
    isMp: 'képviselő',
    sortStart: 'Kezdete szerint',
    sortOffice: 'Tisztség szerint',
    noResults: 'Nincs a feltételeknek megfelelő megbízatás.',
    // A nyilvántartás saját kategóriái (a portál szűrői).
    categories: {
      pm: 'Miniszterelnök',
      minister: 'Miniszter',
      'state-secretary': 'Államtitkár',
      parliamentary: 'Országgyűlési tisztségviselő',
      senior: 'Egyéb vezető tisztség',
      other: 'Egyéb tisztség',
      uncategorised: 'Besorolás nélkül',
    },
  },
  // Tárcák (§6C): a kormányzati oldal – melyik minisztériumhoz milyen kérdés
  // érkezett, és melyik tárca mit nyújtott be.
  portfolios: {
    title: 'Tárcák',
    intro: 'Ez az oldal a minisztériumok alá tartozó kormányzati tisztségeket hivatott egységesíteni.',
    unit: 'tárca',
    searchPlaceholder: 'Keresés tárca neve szerint…',
    scope: 'a(z) {cycle} ciklusban',
    noResults: 'Nincs a feltételeknek megfelelő tárca.',
    backToList: 'Tárcák',
    kinds: {
      ministry: 'Minisztériumok',
      pm: 'Miniszterelnök',
      'no-portfolio': 'Tárca nélküli miniszterek',
      other: 'Egyéb kormányzati tisztségek',
      body: 'Független állami szervek',
    },
    bodyNote: 'Nem a kormány részei.',
    answered: 'megválaszolt kérdés',
    submitted: 'benyújtott iromány',
    speeches: 'felszólalás',
    answeredShort: 'kérdés',
    submittedShort: 'iromány',
    speechesShort: 'felszólalás',
    medianDays: 'nap – írásbeli kérdés megválaszolásának medián ideje',
    holders: 'A tárca vezetői',
    holdersMore: 'További {count} tisztségviselő',
    holdersFewer: 'Kevesebb',
    // A listában a tárca vezetői közül csak néhány fér ki; a többit a profil mutatja.
    leadsMore: '+{count} további',
    trendTitle: 'Megválaszolt kérdések évenként',
    trendCaption: 'A tárca által megválaszolt kérdések száma évenként.',
    emptyPanel: 'Ebben a ciklusban nincs ilyen tétel.',
    noAgenda: 'Napirendi pont nélkül',
    aliases: 'Összevont megnevezések',
    // Amit az adat még nem tud – kimondva, nem a számból kitalálva (MIN-10a).
    answeredNote: 'Egyelőre csak a megválaszolt kérdések szerepelnek: az '
      + 'irományok címzettje még nincs betöltve, így a megválaszolatlanul maradt '
      + 'kérdések nem jelennek meg, és válaszadási arányt sem mutatunk.',
    speechCoverage: 'A felszólalás melletti tisztséget csak azokban a ciklusokban '
      + 'rögzíti a letöltés, amelyeket azóta újratöltöttünk – a többinél nem azt '
      + 'jelenti a nulla, hogy a tárca nem szólalt fel, hanem hogy erről az '
      + 'adatról ott még nincs információnk.',
    methodology: 'A tárcát a forrás saját megnevezéseiből azonosítjuk: a válaszoló '
      + 'tisztség (iromány-esemény), a kormányzati benyújtó („kormány (…)”), a '
      + 'felszólaláshoz rögzített tisztség és a tisztségviselői nyilvántartás '
      + 'megbízatásai. A megnevezéseket kézzel ellenőrzött, nyilvános táblázat '
      + 'kapcsolja tárcákhoz; amit a táblázat nem ismer, az saját néven, önálló '
      + 'tételként jelenik meg, nem olvad össze mással. Az átnevezéseket nem vonjuk '
      + 'össze: a Nemzeti Erőforrás Minisztérium és az Emberi Erőforrások '
      + 'Minisztériuma külön szerepel, mert a jogutódlás nem az adatból következik. '
      + 'A miniszterelnöki biztosi és kormánymegbízotti megbízatások nem tartoznak '
      + 'egyik tárcához sem, ezért kimaradnak.',
  },
  // "Ki a képviselőm?" — település → egyéni választókerület → képviselő (REP-10).
  lookup: {
    title: 'Ki a képviselőm?',
    intro: 'Nem tudod melyik képviselő tartozik hozzád? Add meg a települést, ahol laksz és megmutatjuk, hogy a 2026- ciklusban kihez tartozik!',
    searchLabel: 'Település',
    searchPlaceholder: 'Pl. Debrecen, Pécs, Budapest 09. kerület…',
    searchHint: 'Az ékezetek nem számítanak, és a fővárosi kerületeket „V. kerület” '
      + 'vagy „5. kerület” formában is megtalálja.',
    clear: 'Törlés',
    noSettlement: 'Nincs ilyen település. Próbálja a nevének egy rövidebb részletével.',
    splitBadge: '{count} választókerület',
    singleAnswer: '{name} egésze a(z) {constituency} része.',
    splitAnswer: '{name} területét {count} egyéni választókerület osztja fel.',
    splitHelp: 'Válaszd ki a térképen azt a részt, ahol laksz! Vagy válassz '
      + 'közvetlenül a listából!',
    pickerLabel: 'A település választókerületei',
    pickPrompt: 'Válassz egy választókerületet a képviselő megjelenítéséhez!',
    mapLabel: 'A település választókerületeinek térképe',
    mapAriaFor: '{name} választókerületeinek térképe – kattintson arra a részre, ahol lakik',
    mapFailed: 'A térkép nem tölthető be. A választókerületet az alábbi listából is kiválaszthatja.',
    noGeometry: 'A választókerületi határok most nem érhetők el; válasszon a lista alapján.',
    noMp: 'Ehhez a választókerülethez nem találtunk képviselőt az adatbázisunkban.',
    noConstituency: 'Ehhez a településhez nem találtunk választókerületet.',
    writeEmail: 'E-mail írása',
    copyEmailOf: 'E-mail cím másolása: {email}',
    emailCopied: 'Másolva!',
    emailCopiedOf: '{email} a vágólapra másolva.',
    noEmail: 'Ehhez a képviselőhöz nincs nyilvános e-mail cím.',
    scope: 'A találat a(z) {cycle} ciklusra vonatkozik: a választókerületi határokat '
      + 'minden választás előtt újra megállapíthatják.',
    listNote: 'Az egyéni választókerületi képviselőn kívül az országos listáról '
      + 'bejutott képviselők is az Országgyűlés tagjai, ők azonban nem '
      + 'választókerülethez kötődnek.',
    listNoteLink: 'Az összes képviselő',
    unavailable: 'A választókerületi adatok jelenleg nem érhetők el (a Nemzeti '
      + 'Választási Iroda forrása nem válaszol). Kérjük, próbálja meg később.',
    methodology: 'Módszertan és adatforrás',
    methodologyText: 'A település–választókerület megfeleltetés és a választókerületi '
      + 'határok a Nemzeti Választási Iroda adatai; a képviselők és a mandátumaik a '
      + 'parlament.hu adatai. Az egyéni választókerületek határai választásonként '
      + 'változhatnak, ezért a találat arra a ciklusra vonatkozik, amelyet ez a '
      + 'választás hozott létre – nem a fejlécben kiválasztott ciklusra.',
    sourceLine: 'A választókerületi adatok forrása: {name} –',
  },
  profile: {
    speeches: 'Felszólalások',
    questions: 'Benyújtott kérdések és interpellációk',
    bills: 'Benyújtott törvény- és határozati javaslatok',
    otherDocuments: 'Egyéb benyújtott irományok',
    statistics: 'Statisztikák',
    biography: 'Adatok',
    office: 'Tisztség',
    officeHistory: 'Tisztségek',
    officeTermApprox: 'legalább {range}',
    officeTermSince: 'legalább {date} óta',
    officeTermNote: 'A tisztség betöltésének ideje.',
    officeTermSpeechesNote: 'A tisztség betöltésének pontos ideje nem ismert; a dátumok a felszólalásaiból származnak: ekkor szólalt fel ezzel a tisztséggel.',
    mandate: 'Mandátum',
    // Megszűnt mandátum (REP-14): a ciklus vége előtt véget ért képviselői megbízatás,
    // a parlament.hu szerinti indokkal, és a mandátumot átadó/átvevő képviselővel.
    mandateEndedNote: 'A mandátum a ciklus vége előtt megszűnt.',
    mandateTermNote: 'A képviselői megbízatás időtartama ebben a ciklusban.',
    predecessor: 'Elődje a mandátumban',
    successor: 'Utódja a mandátumban',
    constituency: 'Választókerület',
    education: 'Legmagasabb végzettség',
    email: 'E-mail',
    website: 'Honlap',
    committees: 'Bizottsági tagságok',
    factionHistory: 'Frakciótörténet',
    wikipedia: 'Wikipédia',
    kmonitor: 'K-Monitor',
    cv: 'Önéletrajz',
    assetDeclarations: 'Vagyonnyilatkozatok',
    assetDeclarationsNote: 'A képviselő vagyonnyilatkozatai a parlament.hu-n közzétett formában; minden tétel az eredeti PDF-re mutat. A dátum a vagyoni állapot időpontja, vagyis az az időpont, amelyre a nyilatkozat vonatkozik. A lista minden ciklust tartalmaz, függetlenül a kiválasztott ciklustól.',
    assetDeclarationDate: 'vagyoni állapot: {date}',
    assetDeclarationMissing: 'Nincs közzétett dokumentum',
    assetDeclarationDeadline: 'beadási határidő: {date}',
    // Tiszteletdíj (REP-17). Az ÖSSZEG HIVATALOS: a parlament.hu közli havonta.
    // A törvény csak magyarázza — az alapdíjjal elosztva kijön a szorzó és a §.
    // A felület sosem mondhatja, hogy „ennyit keres”: ez a képviselői tiszteletdíj,
    // költségtérítés és más közszolgálati illetmény nélkül.
    salary: 'Tiszteletdíj',
    salaryPerMonth: 'Ft / hó (bruttó)',
    salaryMonth: '{month} havi tiszteletdíj',
    salarySource: 'Forrás: parlament.hu — a képviselő közzétett havi bruttó tiszteletdíja.',
    salaryBasis: 'Ez a {section} szerinti alapdíj ({amount} Ft) {multiplier}-szerese.',
    salaryBasisSections: 'A törvényben ezt a mértéket a(z) {sections} állapítja meg.',
    salaryBasisRole: 'A képviselő ezt a megbízatást tölti be: {role} ({section}).',
    salaryBasisInexact: 'Ez az összeg nem egész többszöröse az alapdíjnak ({multiplier}-szerese), ezért nem köthető egyetlen törvényi mértékhez sem.',
    salaryBaseFormula: 'Az alapdíj a törvény szerint {formula}; {date} óta hatályos összeg.',
    salaryNote: 'A képviselők havi bruttó tiszteletdíját az Országgyűlés közzéteszi; az itt látható összeg ez a hivatalos adat, nem számított érték. A törvény (2012. évi XXXVI. törvény) csak a magyarázathoz kell: minden tiszteletdíj a 104. § (1) szerinti alapdíj valamilyen többszöröse, így az összeget elosztva az alapdíjjal megkapjuk, melyik törvényi mérték szerint fizetik. Több egyidejű tisztség esetén a törvény a magasabb összegűt rendeli (105. § (7)), az összegek nem adódnak össze.',
    salaryHistory: 'Korábbi hónapok',
    salaryCaveatExpenses: 'Nem tartalmazza a költségtérítéseket (lakhatási, irodai, alkalmazotti és utazási keretek) — azok elszámolás alapján járó térítések, nem jövedelem, és a felhasznált összeg nem nyilvános.',
    salaryCaveatGovernment: 'A képviselő kormányzati tisztséget is betölt: a 106. § (2) szerint a képviselői tiszteletdíj emellett jár, a kormányzati illetményt más törvény állapítja meg. Az itt látható összeg tehát a javadalmazásának csak egy része.',
    salaryCaveatPartialMonth: 'A hónap nem teljes vagy csökkentett tiszteletdíjat mutat: ez lehet év közben kezdődő vagy megszűnő megbízatás töredékhónapja, vagy a 107. § szerinti, távolmaradás miatti levonás.',
    // A tisztségek neve a törvény szóhasználatával, mert a hivatkozott § is az.
    salaryRole: {
      house_speaker: 'az Országgyűlés elnöke',
      faction_leader: 'képviselőcsoport vezetője',
      deputy_speaker: 'az Országgyűlés alelnöke',
      house_steward: 'az Országgyűlés háznagya',
      faction_deputy: 'képviselőcsoportvezető-helyettes',
      committee_chair: 'állandó bizottság elnöke',
      notary: 'az Országgyűlés jegyzője',
      committee_vice: 'állandó bizottság alelnöke',
      multi_committee: 'törvényalkotási bizottsági vagy legalább két állandó bizottsági tagság',
      one_committee: 'állandó bizottsági tagság',
      member: 'képviselői megbízatás',
    },
    // Csillagjegyek (REP-16). Nyíltan játék: a Wikidata-beli születési dátumból
    // származnak, semmilyen elemzési értékük nincs — a kulcsok nyelvsemlegesek,
    // a címke a felület dolga.
    zodiac: 'Csillagjegy',
    chineseZodiac: 'Kínai állatév',
    // A profilon a jegyek spoiler mögött vannak: a lap megnyitásakor csak a ⛎
    // jel látszik, a jegy csak kattintásra. A címke a gomb egyetlen felolvasható
    // tartalma, ezért mondja ki, mi történik (A11Y-1).
    zodiacReveal: 'Csillagjegyek megjelenítése',
    zodiacHide: 'Csillagjegyek elrejtése',
    zodiacNote: 'Csak érdekesség, nem elemzés: a két jegy a képviselő Wikidatában szereplő születési dátumából adódik (a napjegy a dátumból, az állatév a kínai holdújévhez igazítva). Semmiféle összefüggés nincs közte és a képviselő munkája között, és a Parlamonitor semmilyen statisztikát nem épít rá. Ahol nincs pontos (napra megadott) születési dátum, ott nem szerepel jegy.',
    zodiacSign: {
      aries: 'Kos', taurus: 'Bika', gemini: 'Ikrek', cancer: 'Rák',
      leo: 'Oroszlán', virgo: 'Szűz', libra: 'Mérleg', scorpio: 'Skorpió',
      sagittarius: 'Nyilas', capricorn: 'Bak', aquarius: 'Vízöntő', pisces: 'Halak',
    },
    chineseSign: {
      rat: 'Patkány', ox: 'Bivaly', tiger: 'Tigris', rabbit: 'Nyúl',
      dragon: 'Sárkány', snake: 'Kígyó', horse: 'Ló', goat: 'Kecske',
      monkey: 'Majom', rooster: 'Kakas', dog: 'Kutya', pig: 'Disznó',
    },
    totalSpeeches: 'Felszólalások száma',
    totalSpeakingTime: 'Összes beszédidő',
    billsSubmitted: 'Benyújtott önálló indítványok',
    votesAbsent: 'Alkalommal nem szavazott',
    voteBreakdown: 'Szavazási részvétel',
    vbUnit: 'szavazás',
    vbVoted: 'Szavazott',
    vbNovote: 'Nem szavazott',
    vbAbsent: 'Igazoltan távol',
    vbNotPresent: 'Nem volt jelen',
    vbNotMp: 'Nem volt képviselő',
    vbNotMpNote: 'Ezek a szavazások a képviselői mandátuma előtt (vagy után) zajlottak, ezért nem számítanak bele a részvételbe.',
    billsUnavailable: 'A benyújtott indítványok adatai a Törvényjavaslatok modul bevezetése után lesznek elérhetők.',
    activity: 'Aktivitás',
    activityHelp: 'Napi aktivitás: az adott napon elhangzott felszólalások és benyújtott irományok száma. A sötétebb szín nagyobb aktivitást jelez. Az ötletet a GitHub hasonló diagramja adta.',
    activityAriaLabel: 'Aktivitási naptár: {days} aktív nap',
    activityDocs: 'iromány',
    activityWindow: 'Legfeljebb az utolsó 200 nap látható.',
    methodology: 'Módszertan',
    scope: 'Az adatok köre',
    sessionsCovered: 'feldolgozott ülésnap',
    noSpeeches: 'Nincs rögzített felszólalás.',
    speechesDayCount: 'felszólalás',
    speechesLoadError: 'A felszólalások betöltése nem sikerült.',
    // Kilépő link a keresőbe, erre a képviselőre szűrve: a napok szerinti lista
    // böngészhető, de szövegre keresni csak a keresőben lehet.
    searchSpeeches: 'Keresés a felszólalásaiban',
    viewSpeech: 'Megtekintés',
    showMore: 'Továbbiak megjelenítése',
    showLess: 'Kevesebb megjelenítése',
    showingFirst: 'A lista az első {n} elemet mutatja.',
    present: 'jelenleg',
    votes: 'Szavazások',
    votesDayCount: 'szavazás',
    votesLoadError: 'A szavazások betöltése nem sikerült.',
    votesNote: 'A képviselő név szerinti szavazásai a feldolgozott szavazásokon.',
    noVotes: 'Nincs rögzített név szerinti szavazás.',
    viewVote: 'Szavazás megtekintése',
    allVotes: 'Összes szavazás megtekintése',
  },
  // Összehasonlítás (REP-15): felszólalók egymás mellett, terméklap-szerűen.
  compare: {
    title: 'Felszólalók összehasonlítása',
    intro: 'Legfeljebb {max} felszólaló egymás mellett, soronként ugyanaz az adat.',
    tableCaption: 'Összehasonlító táblázat: soronként egy adat, oszloponként egy felszólaló.',
    // A profil halk kilépő linkje a névsor sorában.
    compareAction: 'Összehasonlítás',
    compareWith: 'Összehasonlítás másokkal',
    // Az üres állapot: a lap maga a választó.
    emptyLead: 'Válassz felszólalókat, és tedd őket egymás mellé.',
    emptyHint: 'Bármelyik felszólaló adatlapjáról is elindíthatod az összehasonlítást.',
    addPerson: 'Felszólaló hozzáadása',
    close: 'Bezárás',
    pickerHint: 'Kezdj el írni egy nevet, vagy válassz a ciklus legaktívabb felszólalói közül.',
    alreadyIn: 'már szerepel',
    remove: '{name} eltávolítása az összehasonlításból',
    needTwo: 'Adj hozzá még egy felszólalót, hogy legyen mihez hasonlítani.',
    missing: 'Nem található felszólaló ezzel az azonosítóval: {ids}. Ez az oszlop kimaradt.',
    dropped: 'Egyszerre legfeljebb {n} felszólaló hasonlítható össze; a többi kimaradt.',
    diffOnly: 'Csak a különbségek',
    diffOnlyCount: '{n} egyező sor',
    // Semleges jelölés: a lap azt mondja meg, ki beszélt többet — nem azt, ki jobb.
    largest: 'a legnagyobb érték ebben a sorban',
    // Két különböző üres cella: a fogalom nem alkalmazható erre a személyre,
    // illetve alkalmazható, csak nem tudjuk. Összekeverni őket tárgyi hiba.
    na: 'nem értelmezhető',
    unknown: 'nincs adat',
    sectionWho: 'Kik ők',
    sectionSpeech: 'Felszólalások',
    sectionDocs: 'Benyújtott irományok',
    sectionVotes: 'Szavazás',
    sectionOther: 'Pályakép',
    avgSpeech: 'Átlagos felszólalás-hossz',
    avgSpeechNote: 'Az összes beszédidő elosztva a felszólalások számával: azt mutatja, sok rövid közbeszólás vagy kevés hosszú beszéd áll-e a szám mögött.',
    sentences: 'Mondatok száma',
    speakingDays: 'Ülésnapok felszólalással',
    speakingDaysNote: 'Ahány feldolgozott ülésnapon a felszólaló legalább egyszer szót kapott.',
    ownMotionsNote: 'A parlament.hu saját statisztikája a képviselő önálló indítványairól. Az alatta lévő sorok a Parlamonitor által feldolgozott irományokat számolják típus szerint, ezért a két szám nem feltétlenül egyezik.',
    rollCalls: 'Név szerinti szavazások',
    rollCallsNote: 'Azok a név szerinti szavazások, amelyeken a képviselőnek van rögzített szavazata. A határozatképességi szavazások — a parlament.hu statisztikáihoz hasonlóan — nem számítanak bele.',
    careerNote: 'Ez az adat a teljes pályára vonatkozik, nem csak a kiválasztott ciklusra.',
    profileRow: 'Adatlap',
    openProfile: 'Profil megnyitása',
  },
  factions: {
    title: 'Frakciók',
    subtitle: 'Összesítő statisztikák az egyes frakciókhoz tartozó képviselők felszólalásairól.',
    members: 'képviselő',
    // Plural, for the card link into the filtered MP list (vs. the singular stat label).
    membersLink: 'képviselők',
    speeches: 'felszólalás',
    speakingTime: 'beszédidő',
    avgPerMp: 'átlag / képviselő',
    methodology: 'Módszertan',
  },
  bills: {
    title: 'Törvényjavaslatok',
    subtitle: 'Az Országgyűléshez benyújtott törvényjavaslatok (irományok).',
    figyusz: 'A parlamenti irományok követésére használd a Figyusz! értesítéseit!',
    searchPlaceholder: 'Keresés a címben vagy irományszámban…',
    period: 'Ciklus',
    status: 'Állapot',
    // Gépi besorolás (TOPIC-8) — a szűrő pontosan azokat adja vissza, amelyeken
    // ez a címke látszik.
    topic: 'Téma',
    sortNumber: 'Irományszám szerint',
    sortDate: 'Benyújtás szerint',
    count: 'törvényjavaslat',
    bySponsor: 'Benyújtó',
    viewProfile: 'Képviselő profilja',
    clearSponsor: 'Szűrő törlése',
    noResults: 'Nincs a feltételeknek megfelelő törvényjavaslat.',
    submitterPortfolio: 'A tárca oldala',
    submitters: 'Benyújtók',
    submittedDate: 'Benyújtás dátuma',
    timeline: 'A törvényjavaslat útja',
    timelineNote: 'A jogalkotási szakaszok sorrendben; a megtett lépések kiemelve, a hátralévők halványítva.',
    stageDone: 'megtörtént',
    stageCurrent: 'jelenlegi állapot',
    stagePending: 'még nem történt meg',
    source: 'Forrás',
    openText: 'Iromány szövege (PDF)',
    showDocument: 'Dokumentum megjelenítése',
    hideDocument: 'Dokumentum elrejtése',
    openInNewTab: 'Megnyitás új lapon',
    noText: 'Ehhez az irományhoz nem érhető el letölthető szöveg.',
    viewOnParlament: 'Megtekintés a parlament.hu-n',
    votes: 'Szavazások',
    viewRollCall: 'Név szerinti eredmény',
    yes: 'Igen', no: 'Nem', abstain: 'Tartózkodás', voteTag: 'szavazás',
    // A szavazások listájának kártyáin szereplő származtatott adatok — ugyanaz a
    // szám, ugyanaz a megfogalmazás (ld. votes.attendance / votes.crossVoting).
    attendance: 'Részvétel',
    attendanceTitle: 'Részvétel: {present} leadott szavazat a {seats} képviselői helyből. ' +
      'A „jelen, nem szavazott” és az „igazoltan távol” nem számít részvételnek.',
    crossVoting: 'frakciótól eltérő',
    crossVotingTitle: '{n} képviselő szavazott a saját frakciója álláspontjától eltérően — ' +
      'a leadott szavazatok {pct}-a. Az Országgyűlés hivatalos „frakcióval szemben” adata.',
    events: 'Iromány eseményei',
    speechNumber: 'Felszólalás száma',
    viewSpeech: 'Felszólalás megtekintése',
    videoAnswer: 'Videós válasz',
    openInViewer: 'A teljes felszólalás megnyitása szöveggel',
    debates: 'Vita felszólalásai',
    debatesNote: 'A vita megkezdése és lezárása közötti plenáris felszólalások, sorrendben. Kattints egy felszólalásra a videós megtekintéshez.',
    debateSpeechCount: 'felszólalás',
    noTranscript: 'Nincs szövegleirat',
    showAllSpeeches: 'Mind a(z) {n} felszólalás megjelenítése',
    showAllMotions: 'Mind a(z) {n} iromány megjelenítése',
    showLess: 'Kevesebb megjelenítése',
    committeeEvents: 'Bizottsági események',
    committees: 'Tárgyaló bizottság',
    deadlines: 'Határidők',
    motions: 'Nem önálló irományok',
    motionSummary: 'Nem önálló irományok — összesítés típus szerint',
    documents: 'Indokolások és háttéranyagok',
    date: 'Dátum', event: 'Esemény', committee: 'Bizottság',
    amendment: 'Módosító jav.', report: 'Jelentés',
    name: 'Megnevezés', deadline: 'Határidő', reference: 'Hivatkozás',
    type: 'Típus', valid: 'Érvényes', withdrawn: 'Visszavont', total: 'Összesen',
    meta: {
      subtype: 'Típus', character: 'Jelleg', negotiationMode: 'Tárgyalási mód',
      currentEvent: 'Aktuális iromány esemény', promulgationNumber: 'Kihirdetés száma',
      mkNumber: 'Magyar Közlöny szám', promulgationDate: 'Kihirdetés dátuma',
      lastModifier: 'Utolsó módosító irományszáma', remark: 'Megjegyzés',
      kozlony: 'Magyar Közlöny',
    },
    kozlonyLink: 'Megtekintés a Magyar Közlönyben',
    docKind: { justification: 'Indokolás', background: 'Háttéranyag' },
  },
  documents: {
    title: 'Minden iromány',
    subtitle: 'Az Országgyűléshez benyújtott összes iromány egy listában: törvényjavaslatok, határozati javaslatok, kérdések, beszámolók és minden további típus. A törvényjavaslatoknak és a kérdéseknek saját, részletesebben szűrhető oldala is van.',
    // Egy képviselőre szűrt lista: itt minden iromány típus szerepel, a
    // törvényjavaslatokkal együtt (a képviselői profil statisztikája is így számol).
    sponsorTitle: 'Benyújtott irományok',
    sponsorSubtitle: 'Egy képviselő által benyújtott irományok — minden típus, a törvényjavaslatokkal együtt.',
    bySponsor: 'Benyújtó',
    viewProfile: 'Képviselő profilja',
    clearSponsor: 'Szűrő törlése',
    searchPlaceholder: 'Keresés a címben vagy irományszámban…',
    type: 'Típus',
    period: 'Ciklus',
    status: 'Állapot',
    topic: 'Téma',
    verdict: 'Válasz elfogadása',
    verdictAccepted: 'a képviselő elfogadta a választ',
    verdictRejected: 'a képviselő elutasította a választ',
    verdictHint: 'Csak az interpellációkra vonatkozik.',
    sortNumber: 'Irományszám szerint',
    sortDate: 'Benyújtás szerint',
    count: 'iromány',
    noResults: 'Nincs a feltételeknek megfelelő iromány.',
    submitters: 'Benyújtók',
    answeredBy: 'Válaszolt',
    mainType: {
      T: 'Törvényjavaslatok', H: 'Határozati javaslatok', I: 'Interpellációk',
      K: 'Kérdések', A: 'Azonnali kérdések', B: 'Beszámolók és jelentések',
      S: 'Személyi döntések', Y: 'Tájékoztatók',
    },
  },
  // A kérdéslista (BILL-13) — a Törvényjavaslatok szekció középső füle. Külön
  // névtér a `questions`-től, ami az Elemzések Sankey-jának szövegeit tartja:
  // a két oldal címe azonos, a tartalma nem.
  questionList: {
    title: 'Kérdések',
    subtitle: 'Képviselői kérdések, írásbeli kérdések, interpellációk és azonnali kérdések — ki kérdezte, melyik tárca válaszolt, és mi lett a válasz sorsa.',
    searchPlaceholder: 'Keresés a címben vagy irományszámban…',
    type: 'Kérdés típusa',
    status: 'Állapot',
    topic: 'Téma',
    answer: 'Válasz',
    answerAnswered: 'megválaszolva (bármilyen módon)',
    answerOral: 'szóban megválaszolva',
    answerWritten: 'írásban megválaszolva',
    answerUnanswered: 'nincs válasz',
    // A „nincs válasz” az események hiányát jelenti, nem azt, hogy a tárca
    // elmulasztotta: egy frissen benyújtott kérdésnél még nem is járt le a
    // válaszadási határidő (TRUST-1).
    answerHint: 'A jegyzőkönyvi események alapján. A „nincs válasz” frissen benyújtott kérdésnél azt is jelentheti, hogy a határidő még nem járt le.',
    responder: 'Válaszadó tárca',
    verdict: 'Válasz elfogadása',
    verdictAccepted: 'a képviselő elfogadta a választ',
    verdictRejected: 'a képviselő elutasította a választ',
    verdictHint: 'Csak az interpellációkra vonatkozik.',
    sortNumber: 'Irományszám szerint',
    sortDate: 'Benyújtás szerint',
    count: 'kérdés',
    noResults: 'Nincs a feltételeknek megfelelő kérdés.',
    answeredBy: 'Válaszolt',
    sankeyLead: 'Honnan hová tartanak ezek a kérdések?',
    sankeyLink: 'Kérdések elemzése',
  },
  questions: {
    title: 'Kérdések',
    subtitle: 'Ki kérdez és ki válaszol? A képviselői kérdések, interpellációk és azonnali kérdések útja a kérdező frakciójától a válaszadó tárcáig – igény szerint kérdéstípus szerinti bontásban.',
    count: 'kérdés',
    noResults: 'Ebben a ciklusban nincs adat a kérdésekről.',
    chartCaption: 'A kérdések áramlása a kérdező frakciójától a válaszadóig.',
    clickHint: 'Kattints egy folyamra a mögötte lévő kérdések megjelenítéséhez, vagy egy csomópontra (típus / frakció / válaszadó) az összes hozzá tartozó kérdésért.',
    hideType: 'Kérdéstípus elrejtése',
    showType: 'Kérdéstípus megjelenítése',
    ungroupOther: 'Egyéb tárca szétbontása',
    groupOther: 'Egyéb tárca összevonása',
    openPortfolio: 'A tárca oldala',
    close: 'Bezárás',
    askerHeading: 'Kérdező (frakció)',
    typeHeading: 'Kérdés típusa',
    answererHeading: 'Válaszadó',
    methodology: 'A kérdező a kérdést benyújtó képviselő frakciója; a válaszadó a válaszoló tárca – akár szóban, akár írásban válaszolták meg a kérdést. A forrás a válaszadót tisztség szerint nevezi meg („Belügyminisztérium államtitkára”, „belügyminiszter”), ezeket a Tárcák oldal feloldótáblája vonja össze egyetlen minisztériummá, így egy tárca egyetlen csomópont; amit a táblázat nem ismer, az saját néven marad. A „Kérdéstípus megjelenítése” gombbal egy vezető oszlop kapcsolható be, amely a kérdés típusa (interpelláció, kérdés, azonnali kérdés vagy írásbeli) szerint bontja a folyamot. A csak a legtöbbet válaszoló tárcák jelennek meg külön, a többi az „Egyéb tárca” csomópontban összesül; az „Egyéb tárca szétbontása” gombbal ez a csomópont felnyitható, és minden válaszadó külön sorban jelenik meg. A ciklust a fejléc ciklusválasztója szabja meg.',
    type: {
      I: 'Interpelláció',
      K: 'Kérdés',
      A: 'Azonnali kérdés',
      W: 'Írásbeli',
    },
    node: {
      oral: 'Szóban megválaszolva',
      other: 'Egyéb tárca',
      unnamed: 'Nem megnevezett tárca',
      unanswered: 'Megválaszolatlan',
      nofaction: 'Független / egyéb',
    },
  },
  // Közbeszólások (§6E) — ki szól közbe kinek a felszólalása alatt.
  interjections: {
    title: 'Közbeszólások',
    subtitle: 'Ki kiabál be kinek a felszólalása alatt? A jegyzőkönyvbe szó szerint bekerült közbeszólások irányított hálózata: a nyíl a bekiabálótól a félbeszakított képviselő felé mutat, vastagsága a közbeszólások száma.',
    count: '{n} közbeszólás {people} képviselő között',
    listCount: '{n} közbeszólás',
    noResults: 'Ebben a ciklusban nincs adat a közbeszólásokról.',
    chartCaption: 'Ki szól közbe kinek a felszólalása alatt: a nyíl a közbeszólótól a félbeszakított képviselő felé mutat.',
    clickHint: 'Kattints egy nyílra a mögötte lévő közbeszólások szövegéért, vagy egy képviselőre a hozzá tartozókért – a panel nyilával az irány bármikor megfordítható.',
    topLabel: 'Top {n} képviselő',
    rankLabel: 'Az ábra rangsora',
    rank: {
      total: 'Összesen',
      made: 'Közbeszólt',
      received: 'Kapott közbeszólást',
    },
    shownShareTotal: 'Az ábrán a legtöbbet közbeszóló és legtöbbször félbeszakított {n} képviselő látszik: a köztük elhangzott közbeszólások az összes közbeszólás {share}%-a.',
    shownShareMade: 'Az ábrán a {n} legtöbbet közbeszóló képviselő látszik: a köztük elhangzott közbeszólások az összes közbeszólás {share}%-a.',
    shownShareReceived: 'Az ábrán a {n} legtöbbször félbeszakított képviselő látszik: a köztük elhangzott közbeszólások az összes közbeszólás {share}%-a.',
    unattributed: 'További {n} közbeszólás nevét a jegyzőkönyv úgy írja, hogy nem lehetett egyetlen képviselőhöz kötni – ezek nem szerepelnek az ábrán.',
    anyone: 'Bárki',
    pickFrom: 'A közbeszóló kiválasztása',
    pickTo: 'A félbeszakított képviselő kiválasztása',
    reverse: 'Irány megfordítása',
    reverseNone: 'Ebben az irányban nem hangzott el közbeszólás',
    rowSpeaker: 'Közbeszóló:',
    rowTarget: 'Félbeszakítva:',
    watch: 'Megnézem',
    zoomIn: 'Nagyítás',
    zoomOut: 'Kicsinyítés',
    resetView: 'Nézet visszaállítása',
    fullscreen: 'Teljes képernyő',
    exitFullscreen: 'Kilépés a teljes képernyőből',
    made: 'Közbeszólások száma',
    received: 'Kapott közbeszólások',
    dragHint: 'Az ábra nagyítható és mozgatható; egy képviselő ki is húzható a kuszaságból.',
    close: 'Bezárás',
    methodology: 'A gyorsírói jegyzőkönyv a hangosabb bekiabálásokat zárójelben, szó szerint, a félbeszakított felszólalás szövegébe írja: „(Balla György: Úgy van!)”. A Parlamonitor ezeket emeli ki, és köti össze a két képviselőt: a közbeszólót a zárójelben megnevezett név alapján, a másik oldalon pedig azt, akié a felszólalás. A név csak akkor számít, ha egyetlen képviselőre illik – ha a Házban egyszerre több azonos nevű képviselő ült (például két Tóth István), a közbeszólás megnevezetlen marad, nem tippelünk. Ugyanezt a szabályt használja a jegyzőkönyv-olvasó is, amikor a szövegben a közbeszóló nevét a profiljára linkeli, így az ábra és a jegyzőkönyv ugyanazt mondja.',
    methodologyExclusions: 'Nem számítjuk bele az ülésvezetői felszólalások alatt elhangzott közbeszólásokat: egy szavazási blokk a jegyzőkönyvben egyetlen, órákig tartó levezető elnöki felszólalás, így egy egész délután bekiabálásai oda esnének, és az alelnökök lennének a Ház messze legtöbbet félbeszakított tagjai (ugyanaz a szabály, ami minden más képviselői statisztikából is kihagyja az ülésvezetést). Nem számítjuk azokat sem, amelyeknél a jegyzőkönyv csak az eseményt rögzíti, a szavakat nem („Gulyás Gergely közbeszól.”), és azokat sem, ahol a szavak megvannak, de a bekiabáló nem („Közbeszólások a Fidesz padsoraiból: Nem!”).',
    coverage: 'A kiválasztott ciklusban {extracted} szó szerinti közbeszólás került elő, ebből {attributed} volt egyértelmű névhez köthető; {procedural} ülésvezetői felszólalás alatt hangzott el.',
  },
  // Elemzések (§4E) — a szekció nyitóoldala. A kártyák szövege itt van, a
  // sorrendjük és az, hogy melyik modulhoz tartoznak, a
  // modules/analyses/registry.js-ben.
  analyses: {
    lead: 'Kimutatások a Parlament munkájáról.',
    note: 'Minden elemzés a Parlamonitor saját számítása az Országgyűlés nyilvános '
      + 'adataiból. Mindegyik oldalon ott a módszertan is, hogy pontosan mit mér '
      + 'a szám — és mit nem.',
    needsCycle: 'Ez az elemzés egy cikluson belül értelmezhető: válassz ki egyetlen ciklust.',
    empty: 'Ebben a telepítésben nincs elérhető elemzés.',
    cards: {
      cohesion: {
        source: 'Szavazások alapján',
        title: 'Frakcióelemzés',
        desc: 'Mennyire szavaznak együtt a frakciók, és mennyire tartják magukat '
          + 'a saját soraikhoz — egyezési mátrix, frakciófegyelem és blokktérkép.',
      },
      questions: {
        source: 'Irományok alapján',
        title: 'Kérdések és interpellációk',
        desc: 'Ki kérdez és ki válaszol: a kérdések útja a kérdező frakciójától '
          + 'a válaszadó tárcáig.',
      },
      topics: {
        source: 'Felszólalások és irományok alapján',
        title: 'Témák',
        desc: 'Miről szól a plenáris ülés, és mi kerül irományként a Ház elé — '
          + 'szakpolitikai témák szerint, évről évre és frakciónként.',
      },
      interjections: {
        source: 'Felszólalások alapján',
        title: 'Közbeszólások',
        desc: 'Ki kiabál be kinek a felszólalása alatt — a jegyzőkönyvbe szó '
          + 'szerint bekerült közbeszólások irányított hálózata, a bekiabálások '
          + 'szövegével együtt.',
      },
      settlements: {
        source: 'Felszólalások alapján',
        title: 'Települések',
        desc: 'Mely magyar településeket említik a plenáris ülésen, milyen gyakran '
          + 'és kik — és melyekről nem esett szó soha.',
      },
    },
  },
  votes: {
    title: 'Szavazások',
    subtitle: 'Az Országgyűlés név szerinti és listás szavazásai.',
    cohesionLead: 'Kik szavaznak rendre együtt ezeken a szavazásokon?',
    cohesionLink: 'Frakcióelemzés',
    searchPlaceholder: 'Keresés a tárgyban vagy irományszámban…',
    period: 'Ciklus',
    result: 'Eredmény',
    count: 'szavazás',
    noResults: 'Nincs a feltételeknek megfelelő szavazás.',
    all: 'Mind',
    date: 'Időpont',
    subject: 'A szavazás tárgya',
    votingMode: 'Szavazás módja',
    dateFrom: 'Ettől',
    dateTo: 'Eddig',
    viewOnParlament: 'Megtekintés a parlament.hu-n',
    decidedBills: 'Érintett irományok',
    yes: 'Igen', no: 'Nem', abstain: 'Tartózkodás',
    absent: 'Igazoltan távol', novote: 'Nem szavazott', other: 'Egyéb',
    total: 'Összesen', totalVotes: 'Leadott szavazatok',
    result_: 'Eredmény',
    rollCall: 'Név szerinti szavazás',
    rollCallNote: 'Minden képviselő egyéni szavazata. A képviselő nevére kattintva megnyílik a profilja.',
    noRollCall: 'Ehhez a szavazáshoz nem érhető el név szerinti lista (pl. listás vagy kézfeltartásos szavazás).',
    byFaction: 'Frakciók szerint',
    againstFaction: 'frakciótól eltérő',
    backToList: 'Vissza a szavazásokhoz',
    viewBill: 'Törvényjavaslat megtekintése',
    sortDate: 'Időpont szerint',
    sort: 'Rendezés',
    sortNewest: 'Legújabb elöl',
    sortOldest: 'Legrégebbi elöl',
    sortAttendanceDesc: 'Legnagyobb részvétel elöl',
    sortAttendanceAsc: 'Legkisebb részvétel elöl',
    sortCrossDesc: 'Legtöbb frakciótól eltérő szavazat elöl',
    sortCrossAsc: 'Legkevesebb frakciótól eltérő szavazat elöl',
    crossVoting: 'frakciótól eltérő',
    crossVotingTitle: '{n} képviselő szavazott a saját frakciója álláspontjától eltérően — ' +
      'a leadott szavazatok {pct}-a. Az Országgyűlés hivatalos „frakcióval szemben” adata.',
    crossVotingNote: 'A „frakciótól eltérő” oszlop az Országgyűlés hivatalos adata arról, ' +
      'hány képviselő szavazott az adott frakció álláspontjától eltérően. Azt nem közli, ' +
      'hogy név szerint kik — ezért a névsorban sem jelöljük.',
    attendance: 'Részvétel',
    attendanceTitle: 'Részvétel: {present} leadott szavazat a {seats} képviselői helyből. ' +
      'A „jelen, nem szavazott” és az „igazoltan távol” nem számít részvételnek.',
    clearFilters: 'Szűrők törlése',
    remark: 'Megjegyzés',
    accepted: 'Elfogadva',
    personScopeSuffix: 'szavazatai',
    clearPersonScope: 'Összes szavazás',
    cohesion: {
      title: 'Frakciók együtt- és szétszavazása',
      subtitle: 'Mennyire szavaznak együtt — és mennyire külön — a frakciók a ciklus név szerinti szavazásain.',
      basis: '{n} név szerinti szavazás alapján',
      empty: 'Ehhez a szűréshez nincs elég név szerinti szavazás a frakcióelemzéshez.',
      tab_matrix: 'Egyetértési mátrix',
      tab_bars: 'Sávok',
      tab_map: 'Frakciótérkép',
      matrixCaption: 'Mennyire szavaznak együtt a frakciók — az átló a frakción belüli egység.',
      low: 'ritkán együtt',
      high: 'gyakran együtt',
      cohesion: 'Belső egység',
      cohesionBars: 'Frakción belüli egység (kohézió)',
      agreeWith: 'Együttszavazás ezzel: {faction}',
      mapCaption: 'A közelség azt jelzi, mennyire szavaznak együtt a frakciók.',
      mapHint: 'A tengelyeknek nincs jelentése, csak a buborékok közti távolság számít; a buborék mérete a létszám, teltsége a belső egység.',
      size: 'Létszám',
      methodology: 'Az együttszavazás mértéke annak a valószínűsége, hogy két véletlenszerűen kiválasztott, szavazatot leadó képviselő — egy-egy a két frakcióból — ugyanúgy szavaz (igen/nem/tartózkodás), a szűrt név szerinti szavazásokra átlagolva. Az átló ugyanez egyetlen frakción belül: a belső egység (kohézió). Csak a név szerinti szavazások számítanak (a határozatképességi szavazások nem), és a legalább 2 fős frakciók szerepelnek. A ciklust a fejléc ciklusválasztója szabja meg.',
    },
  },
  // The About copy carries inline links and emphasis, so these paragraphs are
  // rendered with v-html (static, author-written markup — no user input).
  // NB: a literal '@' would be parsed as vue-i18n link syntax; use &#64;.
  // Települések (§6D) — mely helységeket említi a Parlament, és melyeket soha.
  settlements: {
    title: 'Települések',
    intro: 'A parlament országos, de amiről vitázik, az szinte mindig helyi. '
      + 'Itt az látszik, mely magyar településeket említik a plenáris ülésen, '
      + 'milyen gyakran és kik — és mely településekről nem esett szó soha.',
    scope: '{cycle} adatai',
    unit: 'település',
    // A két főszám: a lefedettség és a vakfoltok (TEL-7).
    namedOf: 'említett település ({total}-ból)',
    neverNamed: 'egyszer sem került szóba',
    mentionsTotal: 'említés',
    modeLabel: 'Térkép nézete',
    modeAll: 'Amiről szó van',
    modeBlind: 'Vakfoltok',
    mapLabel: 'Településemlítések térképe',
    mapFailed: 'A térkép nem tölthető be.',
    // A térkép melletti figyelmeztetés: mit mér és mit nem (TEL-7/TEL-12).
    mapCaveat: 'A térkép a plenáris jegyzőkönyvben szereplő említéseket mutatja a '
      + 'kiválasztott ciklus(ok)ra, nem a településre fordított figyelmet: egy '
      + 'falut jól szolgálhatnak úgy is, hogy a Parlament ülésén nem hangzik el a neve. '
      + 'A felismerés szándékosan óvatos, ezért minden szám alsó becslés.',
    noGeometry: 'Ehhez a nézethez nincs elérhető térképi adat.',
    legendBlind: 'nincs említés',
    legendBlindOnly: 'egyszer sem említett település',
    legendScale: 'A körök mérete és színe az említések számát követi, '
      + 'logaritmikus skálán.',
    searchPlaceholder: 'Település keresése…',
    noResults: 'Nincs a szűrésnek megfelelő település.',
    sortBy: 'Rendezés:',
    sortMentions: 'Említés szerint',
    sortName: 'Név szerint',
    sortElectorate: 'Választók száma szerint',
    sortFocus: 'Saját körzet aránya',
    sortCoverage: 'Körzeti lefedettség',
    clearCounty: '{county} szűrő törlése',
    binsLabel: 'Térkép felbontása',
    bins: {
      points: 'Települések',
      oevk: 'Választókerületek',
      h3: 'Szegmensek',
    },
    segmentsUnavailable: 'A szegmentált nézet ezen a kiszolgálón nem érhető el. '
      + 'A települések térképe változatlanul működik.',
    segmentBlindShare: 'nem került szóba',
    segmentCounts: '{named} / {total} település került szóba',
    segmentShared: 'ebből {n} települést más választókerülettel oszt meg',
    segmentOwn: 'saját körzetéből {named} / {total} települést nevezett meg',
    legendSegmentScale: 'Egy-egy szegmens egyenlő területű hatszög; a szín a benne '
      + 'lévő települések összes említését követi, logaritmikus skálán.',
    legendSegmentBlind: 'A szín a szegmens azon településeinek aránya, amelyek egyszer '
      + 'sem kerültek szóba. Az arány mögötti darabszám a szegmensre mutatva látszik.',
    legendOevkScale: 'Egy-egy szín egy egyéni választókerület: a benne lévő '
      + 'települések összes említése, logaritmikus skálán. A kerületek választói '
      + 'létszáma közel egyenlő, a területük nem.',
    legendOevkBlind: 'A szín a választókerület azon településeinek aránya, amelyek '
      + 'egyszer sem kerültek szóba. A darabszám és a képviselő a kerületre mutatva '
      + 'látszik.',
    segmentCaveat: 'Egy szegmensbe néhány település esik, néhányba csak egy — '
      + 'egy-két település fölött az arány már nem arány, ezért minden szegmens '
      + 'megmutatja a mögötte lévő darabszámot is.',
    oevkCaveat: 'A választókerületek választói létszáma közel egyenlő, a területük '
      + 'nem: egy vidéki kerület ugyanannál a számnál sokkal nagyobb felületet fest a '
      + 'térképen, mint egy budapesti. Öt kerületben egyetlen település van, '
      + 'tizenkilencben legfeljebb három — ott az arány mögötti darabszám a lényeg.',
    oevkUnattributed: 'A „{names}” mint egész ({mentions} említés) egyetlen '
      + 'választókerülethez sem tartozik — a főváros tizenhat kerületet fed le —, '
      + 'ezért egyik szín sem tartalmazza; a budapesti kerületek említései igen.',
    oevkOverlap: '{settlements} település (Debrecen, Szeged, Pécs és a megosztott '
      + 'budapesti kerületek) egynél több választókerületbe esik. Egy említés a '
      + 'helyet nevezi meg, nem a kerületrészt, ezért mindegyik érintett kerületnél '
      + 'számoljuk: {mentions} említés szerepel így többször, a színek összege tehát '
      + 'nem az országos összeg.',
    mentionsShort: 'említés',
    neverShort: 'nem került szóba',
    blindByCounty: 'Hol van a legtöbb vakfolt?',
    blindOfTotal: '{blind} / {total} település',
    methodology: 'Az említéseket a Nemzeti Választási Iroda hivatalos '
      + 'településjegyzékét a jegyzőkönyv szövegéhez illesztve keressük, a magyar '
      + 'toldalékokat (Kaposváron, Kaposvárra, kaposvári) is felismerve. Az '
      + 'ülésvezetői és eljárási felszólalások — mint minden statisztikánkból — '
      + 'kimaradnak. A köznyelvi szavakkal vagy személynevekkel egyező '
      + 'településnevek (Baj, Alap, Varga) csak megerősítés mellett számítanak: '
      + 'ha a mondat helyre utaló toldalékot vagy szót tartalmaz, illetve ha a '
      + 'név nem egy felismert személy- vagy szervezetnév része. Ezért a számok '
      + 'alsó becslések, a vakfolt pedig azt jelenti: „nem találtunk említést”, '
      + 'nem azt, hogy biztosan nem hangzott el.',
    sourceNote: 'A települések listája és választókerületi beosztása: {source}. '
      + 'A térképi pontok az OpenStreetMap településpontjai (ODbL), így a pötty ott '
      + 'van, ahol az alaptérkép kiírja a nevet. Az említések a parlament.hu '
      + 'jegyzőkönyveiből származnak.',
    // Egy település lapja (TEL-8)
    notFound: 'Nincs ilyen település.',
    electorate: '{n} választó',
    inSpeeches: 'felszólalásban',
    bySpeakers: 'felszólalótól',
    between: 'Először {first}, utoljára {last}',
    neverInScope: 'A kiválasztott ciklusban nem került szóba.',
    neverExplain: 'Ez a lap maga a vakfolt: a település létezik, csak a plenáris '
      + 'jegyzőkönyvben nem találtunk rá utalást. Más ciklust választva '
      + 'változhat az eredmény.',
    ambiguousCue: 'Ez a településnév köznyelvi szóval vagy személynévvel esik '
      + 'egybe, ezért csak akkor számoljuk említésnek, ha a mondat egyértelműen '
      + 'helyről beszél. Az itt látható szám ezért különösen óvatos.',
    ambiguousSuffix: 'Ez a településnév más szóval is egybeeshet, ezért csak '
      + 'helyre utaló toldalék vagy szó mellett számoljuk említésnek.',
    representedBy: 'Ki képviseli?',
    repUnknownForCycle: 'A(z) {list} képviselőjéről erre a ciklusra nincs adatunk. '
      + 'Korábbi ciklus képviselőjét nem írjuk ide: a kerületi határokat '
      + 'választásonként újrarajzolják, így az már nem ugyanaz a terület.',
    constituencyNote: 'Az egyéni választókerületi beosztás annak a választásnak '
      + 'az adata, amely a jelenlegi térképet létrehozta; a kerületi határokat '
      + 'választásonként újrarajzolják. Országos listán bejutott képviselők is '
      + 'képviselik a települést.',
    constituencyOnly: 'Választókerülete: {list}.',
    trendCaption: 'Említések évenként',
    whoNamedIt: 'Kik említették?',
    citations: 'Elhangzott mondatok',
    watch: 'megnézem',
    searchFor: '„{name}” keresése a jegyzőkönyvben',
    // Saját körzet (TEL-9)
    repsTitle: 'Beszélnek a saját körzetükről?',
    repsIntro: 'Két külön mutató minden egyéni választókerületben megválasztott '
      + 'képviselőre: mennyire a saját körzetéről beszél, és a körzete '
      + 'településeiből mennyit említett egyáltalán.',
    repsCaveat: 'Ez tény, nem szorgalmi rangsor. Egy miniszter az egész országhoz '
      + 'szól, egy belvárosi képviselőnek nincs faluja, amit említhetne, és az '
      + 'alacsony arány nem hanyagság. Országos vagy területi listán bejutott '
      + 'képviselő nem szerepel a listában — nincs körzete, amihez mérni lehetne, '
      + 'és a nulla nem ugyanaz, mint a „nem értelmezhető”.',
    repsFloor: 'legalább {n} településemlítéssel',
    repsUnit: 'képviselő',
    repsEmpty: 'Ehhez a ciklushoz nincs ilyen adat.',
    repsMethodology: 'A „saját körzet aránya” a képviselő településemlítéseiből az, '
      + 'amely a saját választókerületébe esik; a „körzeti lefedettség” a körzete '
      + 'településeiből az, amelyet valaha említett. A kettő szándékosan külön áll: '
      + 'a magas arány + alacsony lefedettség azt jelenti, hogy egy településéről '
      + 'beszél sokat. Budapest mint egész egyik mutatóba sem számít bele (16 '
      + 'választókerületet fed le, így semmit nem mond a saját körzetről), a '
      + 'kerületek viszont igen. A körzet ahhoz a ciklushoz tartozik, amelyben a '
      + 'képviselő a mandátumát viselte.',
    colRep: 'Képviselő',
    colConstituency: 'Választókerület',
    colFocus: 'Saját körzet',
    colCoverage: 'Lefedettség',
    colMentions: 'Említés',
    // A képviselői profil paneljén (TEL-9/REP-3)
    profileTitle: 'Települések',
    profileFocus: 'említése a saját körzetéről',
    profileCoverage: 'körzete településeiből említette',
    profileNoOwn: 'Nincs egyéni választókerülete, ezért a saját körzetre vonatkozó '
      + 'mutatók nem értelmezhetők.',
    profileTop: 'Leggyakrabban említett települések',
    profileOwnTag: 'saját körzet',
    profileEmpty: 'A kiválasztott ciklusban nem említett települést.',
  },
  about: {
    title: 'A projektről',
    body1: 'A <strong>Parlamonitor</strong> a K-Monitor alkalmazása, amely egyszerűen mutatja be az Országgyűlés működését az <a href="https://www.parlament.hu/" target="_blank" rel="noopener">Országgyűlés honlapján</a> közölt adatokon keresztül. Az oldal könnyen áttekinthető és kereshető formában teszi elérhetővé a képviselőkkel, a szavazásokkal és a benyújtott indítványokkal kapcsolatos legfontosabb információkat, illetve ezekből származtatott adatokat, kimutatásokat közöl.',
    body2: 'A projekt célja, hogy közelebb hozza az állampolgárokhoz a parlamenti munkát, és egyszerűbbé tegye annak követését, hogy kik, milyen ügyekben és hogyan vesznek részt a törvényalkotásban, továbbá bátorításképpen szolgáljon arra, hogy minél többen hallassák a hangjukat, fejezzék ki álláspontjukat és szerveződjenek számukra fontos ügyekben.',
    body3: 'A <strong>Parlamonitor</strong> az Országgyűlés honlapján publikált adatok részleges és válogatott újraközlésével hozzájárul a közérdekű adatok terjesztéséhez és megismeréséhez, ezáltal a közhatalom átláthatóbb működéséhez, valamint ahhoz, hogy újságírók, kutatók és érdeklődő állampolgárok tényszerű információk alapján vizsgálhassák az egész országot érintő politikai és szakpolitikai vitákat, illetve az állam törvényalkotó tevékenységét.',
    body4: 'Az oldal jelenlegi (2026 júliusában publikált) változata egy első kísérleti fázis eredménye, az oldalt a következő hónapokban felhasználói visszajelzések alapján és a K-Monitor által elképzelt további funkciókkal bővíteni fogjuk. Az oldallal kapcsolatos visszajelzéseket <a href="https://www.partimap.eu/hu/p/Parlamonitor-visszajelzes/" target="_blank" rel="noopener">itt várjuk</a>.',
    figyusz: 'Az országgyűlési irományok tematikus követésére használd a <a href="https://figyusz.k-monitor.hu/" target="_blank" rel="noopener"><em>Figyusz!</em></a> értesítőjét!',
    dataTitle: 'Az adatok forrása',
    dataBody1: 'A magyar Országgyűlés példásan sok adatot tesz elérhetővé a képviselőkről, a parlamenti vitákról és szavazásokról vagy a jogalkotás folyamatáról, ezek azonban sok esetben nehezen kereshetőek, elemezhetőek. A K-Monitor az <a href="https://parlament.hu" target="_blank" rel="noopener">Országgyűlés honlapján</a> közölt adatokat hasznosítja részlegesen újra, vagy irányítja azokra a felhasználók figyelmét (pl. videók esetében). Noha az Országgyűlés fejlesztők számára üdvözlendő módon <a href="https://www.parlament.hu/web/guest/alkalmazasok" target="_blank" rel="noopener">Web API szolgáltatást</a> tart fenn, amely számos adattípust tesz elérhetővé gép által feldolgozható formátumban, a szolgáltatás jelentős hiányosságokkal rendelkezik, ezért nem használjuk adatforrásként a Parlamonitor projektben.',
    dataBody2: 'Az API számos végpontja nem képes historikus adatok visszaadására. Például a <strong>kepviselok, iromanyok, iromany, szoszolok, bizottsagok, bizottsag</strong> végpontok a dokumentáció alapján nem fogadnak ciklus paramétert. Ezen felül vannak lényeges mezők, amik az API-ból hiányzanak: ilyen a nem önálló indítványok visszaadása, amiről a dokumentáció úgy fogalmaz, hogy <strong>Későbbi fejlesztésre fenntartva</strong>. Felszólalások esetében a ciklus paraméter megadható, azonban a találatok nem tartalmazzák a videó URL-jét, ami a mi felhasználásunkban elengedhetetlen. További gyengeség, amit az API használata során megfigyeltünk, hogy az <strong>iromanyok</strong> endpoint egyszerűen időbeli szűrés lehetősége nélkül adja vissza az aktuális ciklus minden irományát egy XML dokumentumban. Ez az új indoklások követése esetén minden frissítéssel feleslegesen nagy adatforgalmat generál.',
    dataBody3: 'A fent felsorolt hiányosságok miatt a projekthez szükséges adatokat az Országgyűlés weboldalán található egyes tartalmak scrapelése által tesszük a Parlamonitoron elérhetővé. Álláspontunk szerint ez az adathozzáférési mód összhangban van az Országgyűlés honlapjának felhasználási feltételeivel, az információszabadság törvénnyel és a közadatok újrahasznosítására vonatkozó szabályokkal is. Az általunk készített program forráskódja bárki számára szabadon hozzáférhető, reméljük hozzájárul az Országgyűlés honlapjának további fejlesztéséhez is.',
    sourcesTitle: 'Egyéb források',
    sourcesBody1: '',
    sourcesCrow: 'A fejlesztésben nagy segítségünkre volt <a href="https://www.linkedin.com/in/zoltanvarju/" target="_blank" rel="noopener">Varjú Zoltán</a> (<a href="https://crowintelligence.org/" target="_blank" rel="noopener">crow intelligence</a>). Többek közt neki köszönhetjük a <a href="https://saphes.readthedocs.io" target="_blank" rel="noopener">saphes</a> szövegmetrikai könyvtárat, egy <a href="https://crowintelligence.org/parlamonitor-dashboard/" target="_blank" rel="noopener">kísérleti dashboardot</a>, NLP-vel kapcsolatos tanácsadást és általánosabb fejlesztési ötleteket.',
    sourcesBody2: 'A jegyzőkönyvek és felvételek szinkronizálását az <a href="https://openparliament.tv/startseite/" target="_blank" rel="noopener">Open Parliament TV</a> projekt inspirálta.',
    sourcesBody3: 'A szófelhőkhöz szükséges szótövezést a <a href="https://github.com/huspacy/huspacy" target="_blank" rel="noopener">huspacy</a> <a href="https://huspacy.github.io/models/index_md/" target="_blank" rel="noopener">md</a> és <a href="https://huspacy.github.io/models/index_trf/" target="_blank" rel="noopener">trf</a> modelljei végzik. Készítői: György Orosz, Gergő Szabó, Péter Berkecz, Zsolt Szántó, Richárd Farkas',
    sourcesModelsIntro: 'A modellek forrásai:',
    sourcesModel1: 'UD Hungarian Szeged (Richárd Farkas, Katalin Simkó, Zsolt Szántó, Viktor Varga, Veronika Vincze (MTA-SZTE Research Group on Artificial Intelligence))',
    sourcesModel2: 'NYTK-NerKor Corpus (Eszter Simon, Noémi Vadász (Department of Language Technology and Applied Linguistics))',
    sourcesModel3: 'Szeged NER Corpus (György Szarvas, Richárd Farkas, László Felföldi, András Kocsor, János Csirik (MTA-SZTE Research Group on Artificial Intelligence))',
    sourcesModel4: 'Hungarian lg Floret vectors (Szeged AI)',
    sourcesModel5: 'huBERT base model (cased) (Dávid Márk Nemeskey (SZTAKI-HLT))',
    sourcesParlaCap: 'A felszólalások és irományok kategorizálására a <a href="https://huggingface.co/classla/ParlaCAP-Topic-Classifier" target="_blank" rel="noopener">ParlaCap</a> modellt használtuk, melynek szerzői: Taja Kuzman Pungeršek, Peter Rupnik, Daniela Širinić, Nikola Ljubešić.',
    sourcesBody4: 'A képviselők és egyéb entitások linkelése, valamint metaadatainak kiegészítése során a <a href="https://www.wikidata.org/" target="_blank" rel="noopener">Wikidata</a> és a <a href="https://hu.wikipedia.org/" target="_blank" rel="noopener">Wikipédia</a> adatforrásait használjuk.',
    sourcesBody5: 'A videók letöltése során az <a href="https://github.com/ffmpegwasm/ffmpeg.wasm" target="_blank" rel="noopener">ffmpeg.wasm</a> könyvtárat töltjük be, ami <a href="https://github.com/jeromewu" target="_blank" rel="noopener">Jerome Wu</a>-nak köszönhető és az <a href="https://www.ffmpeg.org/" target="_blank" rel="noopener">FFmpeg</a> projekten alapul. Továbbá a Liberation Sans fontot használjuk a ráégetett feliratokhoz.',
    sourcesBody6: 'A képviselő kereső térképéhez a <a href="https://leafletjs.com/" target="_blank" rel="noopener">Leaflet</a> nevű könyvtárat használjuk, ami az <a href="https://www.openstreetmap.org" target="_blank" rel="noopener">OpenStreetMap</a> térképét tölti be. Az OEVK-k településhez kapcsolását és azok polygonjait a vtr.valasztas.hu-ból kinyert adatok alapján végezzük.',
    sourcesBody7: 'A szövegek és videók szinkronizálásához az OpenAI <a href="https://huggingface.co/openai/whisper-large-v3-turbo" target="_blank" rel="noopener">Whisper</a> modelljét használtuk.',
    sourcesBody8: 'A képviselői oldalak aktivitás ábráját a GitHub felhasználói profil felületének hasonló ábrája alapján alakítottuk ki.',
    sourcesBody9: 'A fejlesztés során intenzíven használtuk a Claude Code nevű LLM alapú kódolási asszisztenst a programozás felgyorsítása érdekében.',
  },
  donate: {
    title: 'Támogasd a Parlamonitort',
    description: 'A Parlamonitor a K-Monitor által fejlesztett és fenntartott ingyenes, független projekt. Ha hasznosnak találod, támogasd egyszeri vagy rendszeres adománnyal, hogy hosszú távon is elérhető maradjon és tovább fejlődhessen.',
    amountsLabel: 'Adomány összege',
    paypalButton: 'Adományozok PayPallal',
    moreOptionsButton: 'További támogatási lehetőségek',
    footer: 'Támogatás',
    close: 'Bezárás',
  },
  footer: {
    aboutHeading: 'Parlamonitor',
    about: 'Rólunk',
    contact: 'Kapcsolat',
    lastUpdate: 'Utolsó adatfrissítés',
    kmonitorHome: 'K-Monitor honlap',
    instagram: 'K-Monitor az Instagramon',
    facebook: 'K-Monitor a Facebookon',
    bluesky: 'Parlamonitor a Bluesky-on',
    feedback: 'Visszajelzés',
  },
  // Aktuális napirendek (NR-5). A napirend TERV, nem jegyzőkönyv — a Ház
  // a kiadása és az ülés között pontokat vesz le, sorrendet cserél, időt módosít.
  // Ezért a blokk mindig kiírja, melyik dokumentumból és annak melyik
  // állapotából származik, amit mutat.
  upcoming: {
    title: 'Aktuális napirendek',
    // Ugyanaz a blokk egyetlen ülésnapra szűkítve, a még meg nem tartott
    // ülésnap oldalán (NR-6): ott nem „aktuális napirendek”, hanem ennek az
    // egy napnak a terve.
    dayTitle: 'Tervezett napirend',
    sourceDoc: 'Napirend (PDF)',
    asOf: '{at} órai állapot szerint',
    extraordinary: 'Rendkívüli ülés',
    empty: 'Jelenleg nincs meghirdetett ülésnapirend.',
    startsAt: 'Ülésnap kezdete',
    decisionsFrom: 'Határozathozatalok legkorábban',
    endsAt: 'Várható befejezés',
    showAll: 'További {n} napirendi pont',
    showLess: 'Kevesebb',
    noItems: 'A napirend részletei egyelőre nem olvashatók ki — a dokumentum fent megnyitható.',
    houseCommittee: 'A Házbizottság következő ülése',
    documents: 'További dokumentumok',
    // A Ház eljárási megjegyzéseiből kiolvasott jelölések. Rövidek, mert a
    // napirendi pont mellett jelvényként állnak.
    flags: {
      two_thirds: 'kétharmados',
      four_fifths: 'négyötödös',
      cardinal: 'sarkalatos',
      exceptional: 'kivételes eljárás',
      urgent: 'sürgős tárgyalás',
      derogation: 'házszabálytól eltéréssel',
      nationality: 'nemzetiségi napirendi pont',
      eu: 'uniós napirendi pont',
      quorum: 'határozatképesség szükséges',
      secret_vote: 'titkos szavazás',
    },
  },
  agendaTypes: {
    opening: 'Ülésnap megnyitása',
    procedural: 'Ügyrendi',
    regular: 'Általános vita',
    oath: 'Eskütétel',
    voting: 'Szavazás',
    rules_of_procedure: 'Házszabály',
    questioning_of_the_government: 'Interpelláció',
    qa: 'Azonnali kérdés / kérdés',
    condolence: 'Megemlékezés',
  },
}
