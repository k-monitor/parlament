# parlamonitor

## A projektről

A **Parlamonitor** a K-Monitor alkalmazása, amely egyszerűen mutatja be az
Országgyűlés működését az [Országgyűlés honlapján](https://www.parlament.hu/)
közölt adatokon keresztül. Az oldal könnyen áttekinthető és kereshető formában
teszi elérhetővé a képviselőkkel, a szavazásokkal és a benyújtott indítványokkal
kapcsolatos legfontosabb információkat, illetve ezekből származtatott adatokat,
kimutatásokat közöl.

A projekt célja, hogy közelebb hozza az állampolgárokhoz a parlamenti munkát, és
egyszerűbbé tegye annak követését, hogy kik, milyen ügyekben és hogyan vesznek
részt a törvényalkotásban, továbbá bátorításképpen szolgáljon arra, hogy minél
többen hallassák a hangjukat, fejezzék ki álláspontjukat és szerveződjenek
számukra fontos ügyekben.

A **Parlamonitor** az Országgyűlés honlapján publikált adatok részleges és
válogatott újraközlésével hozzájárul a közérdekű adatok terjesztéséhez és
megismeréséhez, ezáltal a közhatalom átláthatóbb működéséhez, valamint ahhoz,
hogy újságírók, kutatók és érdeklődő állampolgárok tényszerű információk alapján
vizsgálhassák az egész országot érintő politikai és szakpolitikai vitákat,
illetve az állam törvényalkotó tevékenységét.

Az oldal jelenlegi (2026 júliusában publikált) változata egy első kísérleti fázis
eredménye, az oldalt a következő hónapokban felhasználói visszajelzések alapján
és a K-Monitor által elképzelt további funkciókkal bővíteni fogjuk. Az oldallal
kapcsolatos visszajelzéseket
[itt várjuk](https://www.partimap.eu/hu/p/Parlamonitor-visszajelzes/).

Az országgyűlési irományok tematikus követésére használd a
[*Figyusz!*](https://figyusz.k-monitor.hu/) értesítőjét!

### Az adatok forrása

A magyar Országgyűlés példásan sok adatot tesz elérhetővé a képviselőkről, a
parlamenti vitákról és szavazásokról vagy a jogalkotás folyamatáról, ezek azonban
sok esetben nehezen kereshetőek, elemezhetőek. A K-Monitor az
[Országgyűlés honlapján](https://parlament.hu) közölt adatokat hasznosítja
részlegesen újra, vagy irányítja azokra a felhasználók figyelmét (pl. videók
esetében). Noha az Országgyűlés fejlesztők számára üdvözlendő módon
[Web API szolgáltatást](https://www.parlament.hu/web/guest/alkalmazasok) tart
fenn, amely számos adattípust tesz elérhetővé gép által feldolgozható
formátumban, a szolgáltatás jelentős hiányosságokkal rendelkezik, ezért nem
használjuk adatforrásként a Parlamonitor projektben.

Az API számos végpontja nem képes historikus adatok visszaadására. Például a
**kepviselok, iromanyok, iromany, szoszolok, bizottsagok, bizottsag** végpontok a
dokumentáció alapján nem fogadnak ciklus paramétert. Ezen felül vannak lényeges
mezők, amik az API-ból hiányzanak: ilyen a nem önálló indítványok visszaadása,
amiről a dokumentáció úgy fogalmaz, hogy **Későbbi fejlesztésre fenntartva**.
Felszólalások esetében a ciklus paraméter megadható, azonban a találatok nem
tartalmazzák a videó URL-jét, ami a mi felhasználásunkban elengedhetetlen.
További gyengeség, amit az API használata során megfigyeltünk, hogy az
**iromanyok** endpoint egyszerűen időbeli szűrés lehetősége nélkül adja vissza az
aktuális ciklus minden irományát egy XML dokumentumban. Ez az új indoklások
követése esetén minden frissítéssel feleslegesen nagy adatforgalmat generál.

A fent felsorolt hiányosságok miatt a projekthez szükséges adatokat az
Országgyűlés weboldalán található egyes tartalmak scrapelése által tesszük a
Parlamonitoron elérhetővé. Álláspontunk szerint ez az adathozzáférési mód
összhangban van az Országgyűlés honlapjának felhasználási feltételeivel, az
információszabadság törvénnyel és a közadatok újrahasznosítására vonatkozó
szabályokkal is. Az általunk készített program forráskódja bárki számára szabadon
hozzáférhető, reméljük hozzájárul az Országgyűlés honlapjának további
fejlesztéséhez is.

### Egyéb források

A jegyzőkönyvek és felvételek szinkronizálását az
[Open Parliament TV](https://openparliament.tv/startseite/) projekt inspirálta.

A szófelhőkhöz szükséges szótövezést a
[huspacy](https://github.com/huspacy/huspacy)
[md](https://huspacy.github.io/models/index_md/) és
[trf](https://huspacy.github.io/models/index_trf/) modelljei végzik. Készítői:
György Orosz, Gergő Szabó, Péter Berkecz, Zsolt Szántó, Richárd Farkas

A modellek forrásai:

- UD Hungarian Szeged (Richárd Farkas, Katalin Simkó, Zsolt Szántó, Viktor Varga, Veronika Vincze (MTA-SZTE Research Group on Artificial Intelligence))
- NYTK-NerKor Corpus (Eszter Simon, Noémi Vadász (Department of Language Technology and Applied Linguistics))
- Szeged NER Corpus (György Szarvas, Richárd Farkas, László Felföldi, András Kocsor, János Csirik (MTA-SZTE Research Group on Artificial Intelligence))
- Hungarian lg Floret vectors (Szeged AI)
- huBERT base model (cased) (Dávid Márk Nemeskey (SZTAKI-HLT))

A képviselők és egyéb entitások linkelése, valamint metaadatainak kiegészítése
során a [Wikidata](https://www.wikidata.org/) és a
[Wikipédia](https://hu.wikipedia.org/) adatforrásait használjuk.

A videók letöltése során az
[ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm) könyvtárat töltjük be,
ami [Jerome Wu](https://github.com/jeromewu)-nak köszönhető és az
[FFmpeg](https://www.ffmpeg.org/) projekten alapul. Továbbá a Liberation Sans
fontot használjuk a ráégetett feliratokhoz.

A képviselő kereső térképéhez a [Leaflet](https://leafletjs.com/) nevű könyvtárat
használjuk, ami az [OpenStreetMap](https://www.openstreetmap.org) térképét tölti
be. Az OEVK-k településhez kapcsolását és azok polygonjait a vtr.valasztas.hu-ból
kinyert adatok alapján végezzük.

A szövegek és videók szinkronizálásához az OpenAI
[Whisper](https://huggingface.co/openai/whisper-large-v3-turbo) modelljét
használtuk.

A képviselői oldalak aktivitás ábráját a GitHub felhasználói profil felületének
hasonló ábrája alapján alakítottuk ki.

A keresőbe rejtett húsvéti tojás — a kurzort követő cica — az X11-es `oneko`
webes változata, az [oneko.js](https://github.com/tylxr59/oneko.js), amely
[adryd](https://adryd.com) munkája (MIT licenc); a sprite az eredeti oneko
játékból származik.

A fejlesztés során intenzíven használtuk a Claude Code nevű LLM alapú kódolási
asszisztenst a programozás felgyorsítása érdekében.
