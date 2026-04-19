using System.Text.Json;
using NAudio.CoreAudioApi;
using NAudio.Wave;

static int Fail(string message)
{
    Console.Error.WriteLine(message);
    return 1;
}

static Dictionary<string, string> ParseOptions(string[] args, int startIndex)
{
    var options = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    for (var index = startIndex; index < args.Length; index++)
    {
        var current = args[index];
        if (!current.StartsWith("--", StringComparison.Ordinal))
        {
            continue;
        }

        var key = current[2..];
        if (index + 1 >= args.Length)
        {
            throw new ArgumentException($"Missing value for option '{current}'.");
        }

        options[key] = args[index + 1];
        index += 1;
    }

    return options;
}

static string RequireOption(IReadOnlyDictionary<string, string> options, string key)
{
    if (options.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value))
    {
        return value.Trim();
    }

    throw new ArgumentException($"Missing required option '--{key}'.");
}

static MMDevice SelectDevice(
    MMDeviceCollection devices,
    string? requestedName,
    Func<MMDevice> fallbackFactory)
{
    if (!string.IsNullOrWhiteSpace(requestedName))
    {
        var normalized = requestedName.Trim().ToLowerInvariant();
        var exact = devices.FirstOrDefault(device => device.FriendlyName.Trim().ToLowerInvariant() == normalized);
        if (exact is not null)
        {
            return exact;
        }

        var partial = devices.FirstOrDefault(device => device.FriendlyName.Trim().ToLowerInvariant().Contains(normalized));
        if (partial is not null)
        {
            return partial;
        }

        throw new InvalidOperationException($"Audio device was not found: {requestedName}");
    }

    return fallbackFactory();
}

static async Task WaitForStopSignalAsync()
{
    using var reader = new StreamReader(Console.OpenStandardInput());
    while (true)
    {
        var line = await reader.ReadLineAsync();
        if (line is null)
        {
            return;
        }

        if (string.Equals(line.Trim(), "stop", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
    }
}

static async Task<int> RecordAsync(IReadOnlyDictionary<string, string> options)
{
    var microphoneOutputPath = RequireOption(options, "microphone-output");
    var systemOutputPath = RequireOption(options, "system-output");
    var manifestPath = RequireOption(options, "manifest-path");
    var logPath = options.TryGetValue("log-path", out var requestedLogPath) ? requestedLogPath : "";
    var requestedMicrophoneDevice = options.TryGetValue("microphone-device", out var microphoneDeviceName) ? microphoneDeviceName : "";
    var requestedSpeakerDevice = options.TryGetValue("speaker-device", out var speakerDeviceName) ? speakerDeviceName : "";

    Directory.CreateDirectory(Path.GetDirectoryName(microphoneOutputPath)!);
    Directory.CreateDirectory(Path.GetDirectoryName(systemOutputPath)!);
    Directory.CreateDirectory(Path.GetDirectoryName(manifestPath)!);
    if (!string.IsNullOrWhiteSpace(logPath))
    {
        Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
    }

    using var logWriter = string.IsNullOrWhiteSpace(logPath)
        ? null
        : new StreamWriter(logPath, append: true) { AutoFlush = true };

    void Log(string message)
    {
        var line = $"{DateTimeOffset.UtcNow:O} {message}";
        logWriter?.WriteLine(line);
    }

    using var enumerator = new MMDeviceEnumerator();
    var microphoneDevice = SelectDevice(
        enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active),
        requestedMicrophoneDevice,
        () => enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Multimedia)
    );
    var speakerDevice = SelectDevice(
        enumerator.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active),
        requestedSpeakerDevice,
        () => enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia)
    );

    using var microphoneCapture = new WasapiCapture(microphoneDevice);
    using var systemCapture = new WasapiLoopbackCapture(speakerDevice);
    using var microphoneWriter = new WaveFileWriter(microphoneOutputPath, microphoneCapture.WaveFormat);
    using var systemWriter = new WaveFileWriter(systemOutputPath, systemCapture.WaveFormat);

    var microphoneLock = new object();
    var systemLock = new object();
    long microphoneBytes = 0;
    long systemBytes = 0;
    Exception? fatalError = null;
    var microphoneStopped = new TaskCompletionSource<Exception?>(TaskCreationOptions.RunContinuationsAsynchronously);
    var systemStopped = new TaskCompletionSource<Exception?>(TaskCreationOptions.RunContinuationsAsynchronously);

    microphoneCapture.DataAvailable += (_, eventArgs) =>
    {
        try
        {
            lock (microphoneLock)
            {
                microphoneWriter.Write(eventArgs.Buffer, 0, eventArgs.BytesRecorded);
                microphoneWriter.Flush();
                microphoneBytes += eventArgs.BytesRecorded;
            }
        }
        catch (Exception exc)
        {
            fatalError ??= exc;
        }
    };
    systemCapture.DataAvailable += (_, eventArgs) =>
    {
        try
        {
            lock (systemLock)
            {
                systemWriter.Write(eventArgs.Buffer, 0, eventArgs.BytesRecorded);
                systemWriter.Flush();
                systemBytes += eventArgs.BytesRecorded;
            }
        }
        catch (Exception exc)
        {
            fatalError ??= exc;
        }
    };
    microphoneCapture.RecordingStopped += (_, eventArgs) =>
    {
        if (eventArgs.Exception is not null)
        {
            fatalError ??= eventArgs.Exception;
        }

        microphoneStopped.TrySetResult(eventArgs.Exception);
    };
    systemCapture.RecordingStopped += (_, eventArgs) =>
    {
        if (eventArgs.Exception is not null)
        {
            fatalError ??= eventArgs.Exception;
        }

        systemStopped.TrySetResult(eventArgs.Exception);
    };

    var startedAt = DateTimeOffset.UtcNow;
    Log($"capture-start microphone='{microphoneDevice.FriendlyName}' system='{speakerDevice.FriendlyName}'");
    microphoneCapture.StartRecording();
    systemCapture.StartRecording();

    await WaitForStopSignalAsync();

    Log("capture-stop-requested");
    microphoneCapture.StopRecording();
    systemCapture.StopRecording();

    await Task.WhenAll(microphoneStopped.Task, systemStopped.Task);

    lock (microphoneLock)
    {
        microphoneWriter.Flush();
    }

    lock (systemLock)
    {
        systemWriter.Flush();
    }

    var stoppedAt = DateTimeOffset.UtcNow;
    var microphoneSeconds = microphoneBytes / (double)microphoneCapture.WaveFormat.AverageBytesPerSecond;
    var systemSeconds = systemBytes / (double)systemCapture.WaveFormat.AverageBytesPerSecond;

    var manifest = new Dictionary<string, object?>
    {
        ["started_at"] = startedAt.ToString("O"),
        ["stopped_at"] = stoppedAt.ToString("O"),
        ["microphone"] = new Dictionary<string, object?>
        {
            ["path"] = Path.GetFullPath(microphoneOutputPath),
            ["sample_rate"] = microphoneCapture.WaveFormat.SampleRate,
            ["channels"] = microphoneCapture.WaveFormat.Channels,
            ["seconds"] = Math.Round(microphoneSeconds, 3),
            ["device_name"] = microphoneDevice.FriendlyName,
        },
        ["system"] = new Dictionary<string, object?>
        {
            ["path"] = Path.GetFullPath(systemOutputPath),
            ["sample_rate"] = systemCapture.WaveFormat.SampleRate,
            ["channels"] = systemCapture.WaveFormat.Channels,
            ["seconds"] = Math.Round(systemSeconds, 3),
            ["device_name"] = speakerDevice.FriendlyName,
        },
        ["fatal_error"] = fatalError?.ToString(),
    };

    await File.WriteAllTextAsync(
        manifestPath,
        JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true })
    );

    if (fatalError is not null)
    {
        Log($"capture-error {fatalError}");
        return 1;
    }

    Log("capture-finished");
    return 0;
}

static int ListDevices()
{
    using var enumerator = new MMDeviceEnumerator();
    var payload = new Dictionary<string, object?>
    {
        ["microphones"] = enumerator
            .EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active)
            .Select(device => new Dictionary<string, object?>
            {
                ["id"] = device.ID,
                ["name"] = device.FriendlyName,
            })
            .ToList(),
        ["speakers"] = enumerator
            .EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active)
            .Select(device => new Dictionary<string, object?>
            {
                ["id"] = device.ID,
                ["name"] = device.FriendlyName,
            })
            .ToList(),
    };
    Console.WriteLine(JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

if (args.Length == 0)
{
    return Fail("Usage: WindowsAudioRecorder <record|list-devices> [options]");
}

try
{
    switch (args[0].Trim().ToLowerInvariant())
    {
        case "record":
            return await RecordAsync(ParseOptions(args, 1));
        case "list-devices":
            return ListDevices();
        default:
            return Fail($"Unknown command: {args[0]}");
    }
}
catch (Exception exc)
{
    return Fail(exc.ToString());
}
