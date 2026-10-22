/// RsPackager: builds a Rocksmith 2014 CDLC (.psarc) from a DLC Builder project file
/// without opening DLC Builder. Uses the same libraries as DLC Builder (Rocksmith2014.NET),
/// but always builds WITHOUT generating dynamic difficulty.
///
/// Usage:
///   RsPackager build <project.rs2dlc> [--out <dir>] [--wwise <WwiseConsole.exe>] [--mac] [--auto-volume]
///   RsPackager check <project.rs2dlc>
///   RsPackager verify <file.psarc>
///   RsPackager eof <arrangement.xml> --ogg <audio file name> --out <notes.eof>
module RsPackager.Program

open System
open System.IO
open System.Reflection
open Rocksmith2014.Audio
open Rocksmith2014.Common
open Rocksmith2014.Common.Manifest
open Rocksmith2014.DD
open Rocksmith2014.DLCProject
open Rocksmith2014.DLCProject.PackageBuilder
open Rocksmith2014.EOF
open Rocksmith2014.PSARC
open Rocksmith2014.SNG
open Rocksmith2014.XML
open Rocksmith2014.XML.Processing

let private log (msg: string) = Console.WriteLine(msg)

let private fail (msg: string) =
    Console.Error.WriteLine($"ERROR: %s{msg}")
    exit 1

type Options =
    { Command: string
      Target: string
      OutDir: string option
      WwisePath: string option
      Platforms: Platform list
      AutoVolume: bool
      Ogg: string }

let private parseArgs (argv: string array) =
    if argv.Length < 2 then
        fail "Usage: RsPackager build|check|verify <file> [--out DIR] [--wwise PATH] [--mac] [--auto-volume]"

    let rec loop i opts =
        if i >= argv.Length then
            opts
        else
            match argv[i] with
            | "--out" when i + 1 < argv.Length -> loop (i + 2) { opts with OutDir = Some argv[i + 1] }
            | "--wwise" when i + 1 < argv.Length ->
                let path = argv[i + 1]
                loop (i + 2) { opts with WwisePath = (if String.IsNullOrWhiteSpace(path) then None else Some path) }
            | "--mac" -> loop (i + 1) { opts with Platforms = [ PC; Mac ] }
            | "--auto-volume" -> loop (i + 1) { opts with AutoVolume = true }
            | "--ogg" when i + 1 < argv.Length -> loop (i + 2) { opts with Ogg = argv[i + 1] }
            | other -> fail $"Unknown argument: %s{other}"

    loop 2
        { Command = argv[0].ToLowerInvariant()
          Target = Path.GetFullPath(argv[1])
          OutDir = None
          WwisePath = None
          Platforms = [ PC ]
          AutoVolume = false
          Ogg = "guitar.ogg" }

let private loadDefaultBassTone () =
    use stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("default_bass.json")
    (Tone.fromJsonStream stream).Result

/// Adds the DLC Builder default bass tone for any base tone key that has no tone definition.
let private addDefaultTones (project: DLCProject) =
    let missing =
        project.Arrangements
        |> List.choose (function
            | Instrumental i when not (project.Tones |> List.exists (fun t -> t.Key = i.BaseTone)) -> Some i.BaseTone
            | _ -> None)
        |> List.distinct

    let defaultTone = lazy (loadDefaultBassTone ())
    let added = missing |> List.map (fun key -> { defaultTone.Value with Key = key; Name = key })
    { project with Tones = added @ project.Tones }

/// Rocksmith does not detect notes properly in very low bass tunings unless the
/// "low tuning fix" is applied (same as DLC Builder's "Apply Low Tuning Fix").
let private applyLowTuningFix (project: DLCProject) =
    let arrangements =
        project.Arrangements
        |> List.map (function
            | Instrumental inst when inst.RouteMask = RouteMask.Bass
                                     && inst.TuningPitch > 230.0
                                     && inst.Tuning |> Array.exists (fun t -> t < -4s) ->
                log "Applying low bass tuning fix (tuning pitch 220 Hz, strings +12)."
                Instrumental { inst with TuningPitch = inst.TuningPitch / 2.; Tuning = Array.map ((+) 12s) inst.Tuning }
            | other -> other)

    { project with Arrangements = arrangements }

let private validate (project: DLCProject) =
    if project.Arrangements.IsEmpty then fail "The project has no arrangements."
    if not (File.Exists project.AudioFile.Path) then fail $"Audio file not found: %s{project.AudioFile.Path}"
    if project.DLCKey.Length < DLCKey.MinimumLength then fail "DLC key is too short (min 5 characters)."
    if String.IsNullOrWhiteSpace project.Title.Value then fail "Title is empty."
    if String.IsNullOrWhiteSpace project.ArtistName.Value then fail "Artist name is empty."
    if not (File.Exists project.AlbumArtFile) then fail $"Album art not found: %s{project.AlbumArtFile}"

/// Runs DLC Builder's arrangement checks and prints the issues. Returns the number of issues.
let private timeFormat = @"m\:ss\.fff"

let private checkArrangements (project: DLCProject) =
    let mutable count = 0

    for arr in project.Arrangements do
        match arr with
        | Instrumental inst ->
            let xml = InstrumentalArrangement.Load(inst.XmlPath)
            let issues = ArrangementChecker.checkInstrumental xml
            let levels = xml.Levels.Count
            let notes = xml.Levels |> Seq.sumBy (fun l -> l.Notes.Count + l.Chords.Count)
            log $"Arrangement %s{Path.GetFileName inst.XmlPath}: %d{levels} level(s), %d{notes} notes/chords."

            for issue in issues do
                count <- count + 1
                let time =
                    issue.TimeCode
                    |> Option.map (fun t -> TimeSpan.FromMilliseconds(float t).ToString(timeFormat))
                    |> Option.defaultValue "-"
                log $"ISSUE %s{issueCode issue.IssueType} at %s{time}: %A{issue.IssueType}"
        | _ -> ()

    count

let private loadProject (path: string) =
    if not (File.Exists path) then fail $"Project file not found: %s{path}"
    (DLCProject.load path).Result

let private ensurePreview (project: DLCProject) =
    let preview = project.AudioPreviewFile.Path

    if String.IsNullOrWhiteSpace(preview) || not (File.Exists preview) then
        let target =
            if String.IsNullOrWhiteSpace(preview) then Rocksmith2014.Audio.Utils.createPreviewAudioPath project.AudioFile.Path else preview

        let start =
            project.AudioPreviewStartTime
            |> Option.defaultWith (fun () ->
                let length = Rocksmith2014.Audio.Utils.getLength project.AudioFile.Path
                TimeSpan.FromSeconds(max 0.0 (length.TotalSeconds / 2.0 - 14.0)))

        let startText = start.ToString(@"m\:ss")
        log $"Creating preview audio starting at %s{startText}."
        Preview.create project.AudioFile.Path target start
        { project with AudioPreviewFile = { project.AudioPreviewFile with Path = target } }
    else
        project

let private calculateVolumes (project: DLCProject) =
    let volume (file: AudioFile) =
        let v = Volume.calculate file.Path
        log $"Volume for %s{Path.GetFileName file.Path}: %.1f{v} dB"
        { file with Volume = v }

    { project with
        AudioFile = volume project.AudioFile
        AudioPreviewFile = volume project.AudioPreviewFile }

/// Opens a built package and checks that every instrumental SNG has exactly one difficulty level
/// (and, when given, as many notes as the source XML).
let private verifyPackageWith (expectedNotes: int option) (path: string) =
    async {
        use psarc = PSARC.OpenFile(path)
        let platform = if path.EndsWith("_m.psarc", StringComparison.OrdinalIgnoreCase) then Mac else PC
        let sngs = psarc.Manifest |> Seq.filter (fun n -> n.EndsWith(".sng")) |> List.ofSeq
        let wems = psarc.Manifest |> Seq.filter (fun n -> n.EndsWith(".wem")) |> Seq.length
        let mutable ok = true

        for name in sngs do
            use! stream = psarc.GetEntryStream(name) |> Async.AwaitTask
            let! sng = SNG.fromStream stream platform

            if not (name.Contains("vocals")) then
                let maxDiff = if sng.Phrases.Length = 0 then 0 else sng.Phrases |> Array.map _.MaxDifficulty |> Array.max
                let notes = sng.Levels |> Array.sumBy (fun l -> l.Notes.Length)
                log $"VERIFY %s{name}: levels=%d{sng.Levels.Length} maxDifficulty=%d{maxDiff} notes=%d{notes}"
                if sng.Levels.Length <> 1 || maxDiff <> 0 then ok <- false

                match expectedNotes with
                | Some expected when expected <> notes ->
                    log $"VERIFY WARNING: the arrangement XML has %d{expected} notes but the package has %d{notes}"
                | _ -> ()

        log $"VERIFY files=%d{psarc.Manifest.Length} sng=%d{sngs.Length} wem=%d{wems}"
        return ok
    }

let private verifyPackage = verifyPackageWith None

let private xmlNoteCount (project: DLCProject) =
    project.Arrangements
    |> List.tryPick (function
        | Instrumental inst ->
            let xml = InstrumentalArrangement.Load(inst.XmlPath)
            Some(xml.Levels |> Seq.sumBy (fun l -> l.Notes.Count + l.Chords.Count))
        | _ -> None)

let private build (opts: Options) =
    let project = loadProject opts.Target
    validate project

    let project = project |> applyLowTuningFix |> addDefaultTones |> ensurePreview
    let project = if opts.AutoVolume then calculateVolumes project else project
    let issues = checkArrangements project
    if issues > 0 then log $"%d{issues} issue(s) reported by the arrangement checker (see above)."

    let conversion =
        DLCProject.getFilesThatNeedConverting (TimeSpan.FromSeconds 3.0) project
        |> Seq.map (fun file ->
            async {
                log $"Converting %s{Path.GetFileName file} to wem with Wwise..."
                let! _ = Wwise.convertToWem opts.WwisePath file
                return ()
            })
        |> fun tasks -> Async.Parallel(tasks, maxDegreeOfParallelism = 2)
        |> Async.Ignore

    let config =
        { Platforms = opts.Platforms
          BuilderVersion = "RsPackager (Rocksmith2014.NET)"
          Author = project.Author |> Option.defaultValue "make_cdlc"
          AppId = AppId.CherubRock
          GenerateDD = false // the whole point: one level, the full chart, always
          ForcePhraseCreation = false
          DDConfig = { PhraseSearchThreshold = None; LevelCountGeneration = LevelCountGeneration.Simple }
          // DLC Builder's default: crowd events, removal of beats after the audio, EOF fixes...
          // None of these add difficulty levels.
          ApplyImprovements = true
          SaveDebugFiles = false
          AudioConversionTask = conversion
          IdResetConfig = None
          ProgressReporter = None }

    let outDir = opts.OutDir |> Option.defaultValue (Path.GetDirectoryName opts.Target)
    Directory.CreateDirectory(outDir) |> ignore

    let fileName =
        let artist = StringValidator.convertToSortField (StringValidator.FieldType.ArtistName project.ArtistName.Value)
        let title = StringValidator.convertToSortField (StringValidator.FieldType.Title project.Title.Value)
        $"%s{artist}_%s{title}_v%s{project.Version.Replace('.', '_')}" |> StringValidator.fileName

    let target = Path.Combine(outDir, fileName) |> WithoutPlatformOrExtension

    let paths, _ = buildPackages target config project |> Async.RunSynchronously
    let mutable allOk = true

    for path in paths do
        log $"BUILT %s{path}"
        if not (verifyPackageWith (xmlNoteCount project) path |> Async.RunSynchronously) then allOk <- false

    if not allOk then fail "Verification failed: the package contains more than one difficulty level."

[<EntryPoint>]
let main argv =
    let opts = parseArgs argv

    try
        match opts.Command with
        | "build" -> build opts
        | "check" ->
            let project = loadProject opts.Target
            validate project
            checkArrangements project |> ignore
        | "eof" ->
            // An EOF (Editor On Fire) project of the arrangement, for editing the chart by hand.
            let xml = InstrumentalArrangement.Load(opts.Target)
            let target = opts.OutDir |> Option.defaultValue (Path.ChangeExtension(opts.Target, ".eof"))
            let tracks =
                { PartGuitar = Array.empty
                  PartBass = [| { Data = xml; CustomName = Path.GetFileNameWithoutExtension(opts.Target).Replace("_RS2", "") } |]
                  PartBonus = None
                  PartVocals = None }
            EOFProjectWriter.writeEofProject opts.Ogg target tracks
            log $"EOF project: %s{target}"
        | "verify" ->
            if not (verifyPackage opts.Target |> Async.RunSynchronously) then fail "Package has more than one difficulty level."
        | other -> fail $"Unknown command: %s{other}"

        0
    with e ->
        Console.Error.WriteLine($"ERROR: %s{Rocksmith2014.DLCProject.Utils.distinctExceptionMessages e}")
        1
