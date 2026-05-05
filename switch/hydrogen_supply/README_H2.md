# Hydrogen Supply Module

This module extends the Switch power system planning model to represent hydrogen production, liquefaction, storage, and supply. It contains three submodules:

- **Hydrogen Nodes (hydrogen_nodes.py):** It defines the components that are created to enforce the constraints of liquid and gaseous H2 supply, particularly hydrogen demand, balances, and zonal injections/withdrawals.
- **Green Hydrogen (green_hydrogen.py):** models hydrogen production via electrolysis using electricity from the grid.
- **Blue Hydrogen (blue_hydrogen.py):** models hydrogen production from fossil fuels (e.g., natural gas) with CO₂ emissions and carbon capture & storage (CCS).

## Structure
**1. Hydrogen Nodes (hydrogen_nodes.py)**

Defines the hydrogen demand framework and enforces mass balances.

- Demand

    - Reads hydrogen_demand.csv with zonal hydrogen demand per timepoint.
    - Splits total demand into liquid and gaseous portions, based on `--hydrogen-liquid-demand-portion`.

- Balance Constraints

    - Ensures that in each zone and timepoint:
        - Σ injections = Σ withdrawals for gaseous hydrogen.
        - Σ injections = Σ withdrawals for liquid hydrogen.

- Dynamic Lists

    - Liquid_Hydrogen_Zone_Injections / Withdrawals
    - Gaseous_Hydrogen_Zone_Injections / Withdrawals
    - Other modules append their production/consumption terms to these lists.

- Reporting

    Exports balance tables per zone and timepoint:

        - gaseous_hydrogen_demand_balance.csv
        - liquid_hydrogen_demand_balance.csv

**2. Green Hydrogen (green_hydrogen.py)**

- Electrolyzer:

    - Investment and O&M costs (capital, fixed, variable).
    - Conversion efficiency (kg H₂ per MWh).
    - Lifetime and capacity build decisions.
    - Operational variables for running electrolyzers and producing gaseous H₂.

- Liquefier:

    - Investment and O&M costs (capital, fixed, variable).
    - Energy use per kg H₂ liquefied.
    - Variables and constraints for liquefaction flows and capacity.

- Liquid Hydrogen Storage

    - Tank investment costs and minimum size option.
    - Mass balance constraints for inflows, outflows, and stored H₂.
    - Initial conditions and cycling assumptions.

- System Integration

    - Adds electricity consumption of electrolyzers and liquefiers to zone energy balances.
    - Injects gaseous and liquid hydrogen production into the hydrogen network.
    - Adds fixed and variable costs to system cost components.

**3. Blue Hydrogen (blue_hydrogen.py)**

- Blue Hydrogen Infrastructure

    - Multiple candidate projects can be defined per zone.
    - Project-level parameters:
    - Load zone
    - Capital, fixed, variable costs
    - Energy use per kg H₂
    - Fuel source and fuel consumption (MMBTU per kg H₂)
    - Lifetime
    - CO₂ emissions and CCS efficiency

- Production Variables

    - Capacity build decisions per project.
    - Time-dependent production flows (supply, liquefaction).
    - Constraints linking production, liquefaction, and supply.

- Liquefier

    - Project-level liquefaction costs, efficiency, and lifetime.
    - Decision variables for liquefier capacity and operation.

- Liquid Hydrogen Storage

    - Project-level tank costs, minimum size, and lifetime.
    - Variables and constraints for storage mass balances.

- System Integration

    - Adds electricity consumption (process + liquefaction) to energy balances.
    - Adds fuel consumption based on project parameters and fuel prices.
    - Tracks costs (fixed, variable, and fuel).
    - Tracks project-level CO₂ emissions adjusted for CCS efficiency.

- Reporting

    - Outputs a CSV file (blue_h2_emissions.csv) reporting:

        - Period
        - Annual system emissions
        - Annual blue hydrogen emissions (post-CCS)
        - Total combined emissions

## Input Data

Both modules read technology and project parameters from CSV files in the input directory:

    - Hydrogen Demand → hydrogen_demand.csv
    - Green Hydrogen → green_hydrogen.csv
    - Blue Hydrogen → blue_hydrogen_infrastructure.csv

Each file must provide the parameters defined in the corresponding script (capital costs, variable costs, efficiency, lifetime, emissions, etc.).
    
## Usage
There are two options to run Switch with this module. The first one is placing the folder on the directory where the simulations are going to be run, for example in the same directory of the options.txt file and the inputs folder. The second option is to place the folder inside the `switch_model` folder allong with the rest of the modules of Switch.

When the module is correctly placed in order to use it, the user must follow the following steps:

1. Include them in the module list when running Switch: If the selected case to use the module is the first one, the user should add in `modules.txt` the following:
    
    `hydrogen_supply.hydrogen_nodes`
    `hydrogen_supply.green_hydrogen`
    `hydrogen_supply.blue_hydrogen`

2. Provide the required input CSV files with project/technology parameters and the hydrogen consumption profile in a timepoints scope profile.

3. Set on the options file (`options.txt`) the portion of the hydrogen demand that has to be supplied by liquid hydrogen (values between 0 and 1), using the flag `--hydrogen-liquid-demand-portion <Portion>`


# Detailed list of needed parameters

## 1. Hydrogen Nodes (`hydrogen_nodes.py`)

* **zone\_hydrogen\_demand\_kg\[z,t]** – Total hydrogen demand in zone `z` at timepoint `t` (kg).
* **zone\_liquid\_hydrogen\_demand\_kg\[z,t]** – Portion of demand that must be met with liquid hydrogen (kg).

  * Computed as: `zone_hydrogen_demand_kg * hydrogen_liquid_demand_portion`.
* **zone\_gaseous\_hydrogen\_demand\_kg\[z,t]** – Portion of demand that must be met with gaseous hydrogen (kg).

  * Computed as: `zone_hydrogen_demand_kg * (1 - hydrogen_liquid_demand_portion)`.
* **--hydrogen-liquid-demand-portion** (CLI flag) – Fraction (0–1) of total demand required in liquid form.


## 2. Green Hydrogen (`green_hydrogen.py`)

### Electrolyzers

* **hydrogen\_electrolyzer\_capital\_cost\_per\_mw** – Capital cost of electrolyzer capacity (\$/MW).
* **hydrogen\_electrolyzer\_fixed\_cost\_per\_mw\_year** – Fixed annual O\&M cost per MW (\$/MW-yr).
* **hydrogen\_electrolyzer\_variable\_cost\_per\_kg** – Variable cost per kg of hydrogen produced (\$/kg).
* **hydrogen\_electrolyzer\_kg\_per\_mwh** – Efficiency of electrolyzer (kg H₂ per MWh consumed).
* **hydrogen\_electrolyzer\_life\_years** – Lifetime of electrolyzer (years).

### Liquefiers

* **hydrogen\_liquifier\_capital\_cost\_per\_kg\_per\_hour** – Capital cost per kg/hr throughput capacity (\$/(kg/hr)).
* **hydrogen\_liquifier\_fixed\_cost\_per\_kg\_hour\_year** – Fixed annual O\&M cost per kg/hr capacity (\$/(kg/hr)-yr).
* **hydrogen\_liquifier\_variable\_cost\_per\_kg** – Variable cost per kg of hydrogen liquefied (\$/kg).
* **hydrogen\_liquifier\_mwh\_per\_kg** – Electricity consumption of liquefier (MWh per kg H₂).
* **hydrogen\_liquifier\_life\_years** – Lifetime of liquefier (years).

### Liquid Hydrogen Storage

* **liquid\_hydrogen\_tank\_capital\_cost\_per\_kg** – Capital cost of liquid storage tanks (\$/kg capacity).
* **liquid\_hydrogen\_tank\_minimum\_size\_kg** – Minimum build size for a tank (kg).
* **liquid\_hydrogen\_tank\_life\_years** – Lifetime of tank (years).


## 3. Blue Hydrogen (`blue_hydrogen.py`)

### General Project Parameters

Each project is identified in `BLUE_HYDROGEN_PROJECTS`, which is the first column of the input file.

* **blue\_hydrogen\_load\_zone\[h]** – Zone where project `h` is located.
* **blue\_hydrogen\_infrastructure\_capital\_cost\_per\_kg\_per\_hr\[h]** – Capital cost per kg/hr throughput capacity (\$/(kg/hr)).
* **blue\_hydrogen\_infrastructure\_fixed\_cost\_per\_kg\_year\[h]** – Fixed annual O\&M cost per kg capacity (\$/kg-yr).
* **blue\_hydrogen\_infrastructure\_variable\_cost\_per\_kg\[h]** – Variable cost per kg H₂ produced (\$/kg).
* **blue\_hydrogen\_infrastructure\_mwh\_per\_kg\[h]** – Electricity consumption per kg H₂ produced (MWh/kg).
* **blue\_hydrogen\_fuel\_source\[h]** – Fuel type used for production (e.g., Natural Gas).
* **blue\_hydrogen\_infrastructure\_fuel\_consumption\_mmbtu\_per\_kg\[h]** – Fuel required per kg H₂ (MMBTU/kg).
* **blue\_hydrogen\_infrastructure\_life\_years\[h]** – Lifetime of the project infrastructure (years).
* **blue\_hydrogen\_emissions\_tonCO2\_per\_kg\[h]** – Direct CO₂ emissions before CCS (tons CO₂/kg H₂).
* **blue\_hydrogen\_CCS\_efficiency\[h]** – Fraction of CO₂ captured (0–1).

### Liquefiers

* **blue\_hydrogen\_liquifier\_capital\_cost\_per\_kg\_per\_hour\[h]** – Capital cost per kg/hr liquefaction capacity (\$/(kg/hr)).
* **blue\_hydrogen\_liquifier\_fixed\_cost\_per\_kg\_hour\_year\[h]** – Fixed annual O\&M per liquefaction capacity (\$/(kg/hr)-yr).
* **blue\_hydrogen\_liquifier\_variable\_cost\_per\_kg\[h]** – Variable cost per kg H₂ liquefied (\$/kg).
* **blue\_hydrogen\_liquifier\_mwh\_per\_kg\[h]** – Electricity use per kg liquefied (MWh/kg).
* **blue\_hydrogen\_liquifier\_life\_years\[h]** – Liquefier lifetime (years).

### Liquid Hydrogen Storage

* **liquid\_blue\_hydrogen\_tank\_capital\_cost\_per\_kg\[h]** – Capital cost of liquid tank (\$/kg capacity).
* **liquid\_blue\_hydrogen\_tank\_minimum\_size\_kg\[h]** – Minimum tank build size (kg).
* **liquid\_blue\_hydrogen\_tank\_life\_years\[h]** – Tank lifetime (years).
