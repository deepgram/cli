# Performance Timing Architecture

## Overview

The deepctl CLI includes a comprehensive performance timing system that allows users to trace the time each area of the CLI takes between a user command and the output. This helps identify performance bottlenecks and understand where time is being spent during command execution.

## Features

- **Granular Timing**: Tracks timing for different phases of CLI execution
- **Hierarchical Context**: Supports nested timing contexts for detailed analysis
- **Two Output Modes**: Simple and detailed timing reports
- **Thread-Safe**: Uses thread-local storage for concurrent execution
- **Zero Overhead**: When disabled, timing has minimal performance impact

## Usage

### Basic Timing

To see basic timing information for any command:

```bash
deepctl --timing transcribe audio.wav
```

### Detailed Timing

For comprehensive timing breakdown:

```bash
deepctl --timing-detailed transcribe audio.wav
```

## Architecture

### Core Components

#### TimingContext

A context manager that automatically tracks execution time:

```python
from deepctl_core import TimingContext

with TimingContext("operation_name"):
    # Your code here
    pass
```

#### TimingCollector

Thread-local collector that manages all timing measurements:

- Stores timing entries with start/end times
- Maintains parent-child relationships for nested contexts
- Provides summary and reporting functionality

### Timing Points

The CLI tracks timing for these key areas:

#### 1. Startup Phase

- **total_execution**: Complete command execution from start to finish
- **argument_preprocessing**: Command line argument parsing and preprocessing
- **cli_initialization**: Configuration setup and output formatting

#### 2. Loading Phase

- **plugin_loading**: Overall plugin and command discovery
- **builtin_commands_loading**: Loading built-in commands
- **external_plugins_loading**: Loading external plugins
- **discover_entry_points**: Entry point discovery
- **load*command*{name}**: Individual command loading

#### 3. Command Execution Phase

- **command\_{name}\_total**: Complete command execution
- **command_setup**: Configuration and client setup
- **authentication_check**: Authentication validation
- **project_validation**: Project ID validation
- **command\_{name}\_handler**: The actual command logic

#### 4. Output Phase

- **output_processing**: Overall output handling
- **result_formatting**: Data serialization and formatting
- **output\_{format}**: Format-specific output generation (json, yaml, table, csv)

## Implementation Details

### Thread-Local Storage

The timing system uses Python's `threading.local()` to ensure thread safety:

```python
_timing_data = local()

def get_timing_collector() -> TimingCollector:
    if not hasattr(_timing_data, 'collector'):
        _timing_data.collector = TimingCollector()
    return _timing_data.collector
```

### Context Management

Timing contexts are automatically managed using Python's context manager protocol:

```python
@contextmanager
def TimingContext(name: str, metadata: Optional[Dict[str, Any]] = None):
    collector = get_timing_collector()
    collector.start_timing(name, metadata)
    try:
        yield
    finally:
        collector.end_timing(name)
```

### Integration Points

#### Main CLI Entry Point

```python
def main() -> None:
    try:
        with TimingContext("total_execution"):
            with TimingContext("argument_preprocessing"):
                args = sys.argv[1:]
                processed_args = preprocess_hyphenated_commands(args)

            with TimingContext("cli_execution"):
                cli(args=processed_args, standalone_mode=True)

        print_timing_summary()
    except Exception as e:
        # Error handling
```

#### Base Command Execution

```python
def execute(self, ctx: click.Context, **kwargs: Any) -> None:
    with TimingContext(f"command_{self.name}_total"):
        with TimingContext("command_setup"):
            # Setup configuration and clients

        if self.requires_auth:
            with TimingContext("authentication_check"):
                # Authentication logic

        with TimingContext(f"command_{self.name}_handler"):
            result = self.handle(config, auth_manager, client, **kwargs)

        if result is not None:
            with TimingContext("output_processing"):
                self.output_result(result, config)
```

## Output Format

### Simple Mode (--timing)

Shows only top-level timing information:

```
⏱️  Performance Timing Summary
Total execution time: 1234.56ms (1.2346s)

total_execution: 1234.56ms (100.0%)
plugin_loading: 456.78ms (37.0%)
command_transcribe_total: 678.90ms (55.0%)
```

### Detailed Mode (--timing-detailed)

Shows all timing measurements with hierarchy:

```
⏱️  Performance Timing Summary
Total execution time: 1234.56ms (1.2346s)

total_execution: 1234.56ms (100.0%)
  └─ argument_preprocessing: 12.34ms (1.0%)
  └─ cli_execution: 1222.22ms (99.0%)
plugin_loading: 456.78ms (37.0%)
  └─ builtin_commands_loading: 400.00ms (32.4%)
  └─ external_plugins_loading: 56.78ms (4.6%)
command_transcribe_total: 678.90ms (55.0%)
  └─ command_setup: 23.45ms (1.9%)
  └─ authentication_check: 34.56ms (2.8%)
  └─ command_transcribe_handler: 567.89ms (46.0%)
  └─ output_processing: 53.00ms (4.3%)
```

## Performance Considerations

### Minimal Overhead

When timing is disabled (default), the system has minimal overhead:

- No timing collection occurs
- Context managers become no-ops
- No memory allocation for timing data

### Memory Usage

The timing system uses minimal memory:

- One `TimingEntry` per timed operation
- Thread-local storage prevents memory leaks
- Automatic cleanup when threads terminate

### Accuracy

Uses `time.perf_counter()` for high-precision timing:

- Monotonic clock unaffected by system time adjustments
- Nanosecond precision on most systems
- Suitable for measuring short operations

## Extension Points

### Adding Custom Timing

Commands can add their own timing points:

```python
from deepctl_core import TimingContext

def handle(self, config, auth_manager, client, **kwargs):
    with TimingContext("custom_operation"):
        # Your custom operation
        result = do_something()

    with TimingContext("data_processing"):
        # Process the result
        processed = process_data(result)

    return processed
```

### Metadata Support

Timing contexts can include metadata:

```python
with TimingContext("api_request", {"endpoint": "/v1/listen", "method": "POST"}):
    response = client.make_request()
```

## Future Enhancements

- **Export Options**: JSON/CSV export for analysis
- **Thresholds**: Highlight slow operations
- **Comparative Analysis**: Compare timing across runs
- **Integration**: Metrics collection for monitoring systems
