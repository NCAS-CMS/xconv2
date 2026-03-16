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


#### Alpha

We intend to release this as a standalone application (an "app") eventually, but meanwhile, you will likely need a dedicated (or up-to-date) mamba environment with a Python 3.12 (or later) environment with `cartopy` and `udunits2` installed, then you can pip install from PyPI. Here's an example:

```
conda create -n xconv2
conda activate xconv2
mamba install -c conda-forge pip cartopy udunits2
pip install xconv2
```

and then you should have `xconv2` available on your command line in that environment.  You could of course pip install into your own environment, but you will need to ensure it has cartopy and udunits2 in it (both via conda or mamba).

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

## License

This project is licensed under the MIT License. See `LICENSE`.
