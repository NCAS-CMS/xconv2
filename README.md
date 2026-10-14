# xconv2

xconv2 provides (or will provide) a graphical interface to local and remote weather and climate data.  It is a replacement for the venerable [xconv](https://ncas-cms.github.io/xconv-doc/html/index.html) package that has been supported by NCAS CMS for decades.

<table>
  <tr>
    <td style="vertical-align: middle; padding: 2px;">
      <img src="xconv2/assets/under-construction.svg" alt="Under construction icon" height="128px" />
    </td>
    <td style="vertical-align: middle;">
      This version is under development. If you encounter something that looks like it should work, but doesn't, please
      raise a bug issue at the address found in the About menu. If there is function that you would like to see, feel
      free to raise an enhancement request at the same location.
    </td>
  </tr>
</table>

## Installation

We intend to release this as a standalone executable for linux and macos, but for the moment you should use a Python 3.12 (or later) environment with
udunits2 and cartopy installed (from conda-forge), then you can pip install from the source using one of these:

### Alpha

We intend to release this as a standalone application (an "app") eventually, but meanwhile, you will likely need a dedicated (or up-to-date) mamba environment with a Python 3.12 (or later) environment with `cartopy` and `udunits2` installed, then you can pip install from PyPI. Here's an example:

```bash
conda create -n xconv2
conda activate xconv2
mamba install -c conda-forge pip cartopy udunits2
pip install xconv2
```

and then you should have `xconv2` available on your command line in that environment.  You could of course pip install into your own environment, but you will need to ensure it has cartopy and udunits2 in it (both via conda or mamba).

### Conda Environments

- `environment.yml`: base development/runtime environment.
- `environment-integration.yml`: optional integration-test environment, including
  pytest and cryptography for local HTTPS tests. S3 integration tests use the native
  SeaweedFS `weed` binary, installed separately. No Docker engine, CLI, or Python
  Docker/MinIO packages are needed by the application or tests.

See [Running tests](docs/dev_notes.md#running-tests) for setup and skip conditions.

## Documentation

No user documentation is yet available, but it is designed to be intuitive
and basic usage should be possible without documentation once it is installed.

### Raising issues

Please raise any issues using the github link from the
help menu. In doing so, it would be helpful to report
the science stack used from the science stack report
also available from the help menu.



### Developer Documentation

Limited developer documentation is available. For the moment it is
limited to the following:

#### UML Diagrams

Rendered diagrams (GitHub-friendly):

- `docs/uml/svg/CoreWindowCurrentClass.svg`
- `docs/uml/svg/MainWindowComponentMap.svg`
- `docs/uml/svg/CoreWindowGuiWorkerSignals.svg`
- `docs/uml/svg/CoreWindowPhase3OptionsSequence.svg`
- `docs/uml/svg/RemoteWorkerWarmupSequence.svg`
- `docs/uml/svg/RemoteAccessModuleCurrent.svg`

PlantUML sources:

- `docs/uml/alpha_core_window.pu`
- `docs/uml/main_window_component_map.pu`
- `docs/uml/core_window_gui_worker_signals.puml`
- `docs/uml/core_window_options_sequence.puml`
- `docs/uml/remote_worker_warmup_sequence.puml`
- `docs/uml/remote_access_module.puml`

Regenerate SVGs after editing source diagrams:

```bash
./tools/generate_uml_svgs.sh
```

#### Architecture Notes

- `docs/architecture/core_window_refactor.md`
- `docs/architecture/remote_navigation_and_worker_warmup.md`

## License

This project is licensed under the MIT License. See `LICENSE`.
