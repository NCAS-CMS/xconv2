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
udunits2 installed, then you can pip install from the source using one of these:

`pip install "git+https://github.com/ncas-cms/xconv2.git"`
`pip install "git+ssh://github.com/ncas-cms/xconv2.git"`

and then you should have `xconv2` available on your command line.

### Conda Environments

- `environment.yml`: base development/runtime environment (no Docker test dependencies).
- `environment-integration.yml`: optional integration-test environment (includes Python `docker` and `minio` packages for Docker-backed MinIO tests).

## Documentation

No user documentation is yet available, but it is designed to be intuitive
and basic usage should be possible without documentation once it is installed.

### Developer Documentation

Limited developer documentation is available. For the moment it is
limited to the following:

#### UML Diagrams

- `docs/uml/alpha_core_window.puml`
- `docs/uml/core_window_gui_worker_signals.puml`
- `docs/uml/core_window_options_sequence.puml`
- `docs/uml/remote_worker_warmup_sequence.puml`

#### Architecture Notes

- `docs/architecture/core_window_refactor.md`
- `docs/architecture/remote_navigation_and_worker_warmup.md`

## License

This project is licensed under the MIT License. See `LICENSE`.
